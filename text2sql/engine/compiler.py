"""Deterministic SQL compiler: Semantic Query IR -> (sql, params).

Pure function, no I/O and no LLM. Two paths:

* single base table  -> SELECT ... GROUP BY, with INNER JOINs to dimension
  tables (e.g. dim_store) resolved from declared relationships.
* multiple base tables (metrics from 2+ tables, e.g. fact_sales + fact_budget)
  -> one aggregated subquery per base table grouped by the shared keys, then
  the aggregates are joined on those keys. This is the fan-out guard: a budget
  row is never joined to a raw sales line, so nothing double-counts.

Filter values are always emitted as bound parameters.
"""

from __future__ import annotations

from ..semantic.model import Dimension, SemanticModel
from .dialects.base import Dialect
from .ir import Filter, SemanticQuery


class CompileError(Exception):
    pass


def compile(ir: SemanticQuery, model: SemanticModel, dialect: Dialect):
    metrics = [model.metric(m) for m in ir.metrics]
    dims = [model.dimension(d) for d in ir.dimensions]
    metric_tables = {m.table for m in metrics}

    if not metrics and not dims:
        raise CompileError("query must reference at least one metric or dimension")

    if len(metric_tables) <= 1:
        return _compile_single(ir, model, dialect, metrics, dims)
    return _compile_multibase(ir, model, dialect, metrics, dims)


# ----------------------------------------------------------------------------
# single base table
# ----------------------------------------------------------------------------
def _compile_single(ir, model, dialect, metrics, dims):
    qi = dialect.quote_ident
    params: list = []

    if metrics:
        base = next(iter({m.table for m in metrics}))
    else:
        base = dims[0].table
    base_phys = model.table(base).table

    def col(d: Dimension) -> str:
        return _col_sql(model, d, dialect, qualify=True)

    needed_tables = {d.table for d in dims}
    for f in ir.filters:
        needed_tables.add(_field_table(model, f.field))
    if ir.time:
        needed_tables.add(model.dimension(ir.time.field).table)
    for m in metrics:
        needed_tables.update(m.joins)  # a metric's sql may read a joined table
    needed_tables.discard(base)

    select_parts = [f"{col(d)} AS {qi(d.name)}" for d in dims]
    select_parts += [f"({m.sql}) AS {qi(m.name)}" for m in metrics]

    sql = f"SELECT {', '.join(select_parts)}\nFROM {qi(base_phys)}"
    for t in sorted(needed_tables):
        rel = model.relationship_between(base, t)
        sql += "\n" + _join_clause(qi, model, base, t, rel)

    where, where_params = _where(ir, model, dialect, qualify=True)
    params += where_params
    if where:
        sql += f"\nWHERE {where}"

    if dims:
        sql += f"\nGROUP BY {', '.join(col(d) for d in dims)}"

    having, having_params = _having(ir, model, dialect)
    params += having_params
    if having:
        sql += f"\nHAVING {having}"

    order = _order_by(ir, qi)
    if order:
        sql += f"\nORDER BY {order}"
    if ir.limit is not None:
        sql += f"\n{dialect.limit_clause(ir.limit)}"

    return sql, params


def _having(ir, model, dialect):
    """HAVING clause: each filter is on a metric, compared to its aggregate
    expression. Values are bound params, same as WHERE."""
    clauses: list[str] = []
    params: list = []
    for f in ir.having:
        m = model.metric(f.field)  # HAVING filters aggregated metrics only
        clause, p = _filter_sql(f"({m.sql})", f, dialect)
        clauses.append(clause)
        params += p
    return " AND ".join(clauses), params


def _join_clause(qi, model, base, other, rel):
    other_phys = qi(model.table(other).table)
    base_phys = qi(model.table(base).table)
    conds = []
    for from_col, to_col in rel.column_pairs:
        # base may sit on either side of the declared relationship
        base_col, other_col = (
            (from_col, to_col) if rel.from_table == base else (to_col, from_col)
        )
        conds.append(f"{base_phys}.{qi(base_col)} = {other_phys}.{qi(other_col)}")
    return f"JOIN {other_phys} ON {' AND '.join(conds)}"


# ----------------------------------------------------------------------------
# multiple base tables (fan-out guard)
# ----------------------------------------------------------------------------
def _compile_multibase(ir, model, dialect, metrics, dims):
    qi = dialect.quote_ident
    params: list = []

    if ir.having:
        raise CompileError(
            "HAVING is not supported yet for metrics from multiple tables"
        )

    # every group-by dimension must be a key shared by all base tables
    bases = sorted({m.table for m in metrics})
    key_cols = []  # (dim_name, column)
    for d in dims:
        for b in bases:
            if d.column not in model.physical_columns(b):
                raise CompileError(
                    f"cannot group multi-table query by {d.name!r}: not present "
                    f"on base table {b!r} (only shared keys are supported)"
                )
        key_cols.append((d.name, d.column))

    ctes = []
    for b in bases:
        b_metrics = [m for m in metrics if m.table == b]
        b_phys = qi(model.table(b).table)
        sel = [f"{qi(c)} AS {qi(name)}" for name, c in key_cols]
        sel += [f"({m.sql}) AS {qi(m.name)}" for m in b_metrics]
        cte_sql = f"SELECT {', '.join(sel)}\n  FROM {b_phys}"

        cond, cond_params = _where_for_base(ir, model, dialect, b, key_cols)
        params += cond_params
        if cond:
            cte_sql += f"\n  WHERE {cond}"
        if key_cols:
            cte_sql += f"\n  GROUP BY {', '.join(qi(c) for _, c in key_cols)}"
        ctes.append((f"agg_{b}", cte_sql))

    with_block = "WITH " + ",\n".join(
        f"{name} AS (\n  {body}\n)" for name, body in ctes
    )

    first = ctes[0][0]
    select_parts = [f"{first}.{qi(name)} AS {qi(name)}" for name, _ in key_cols]
    # metrics keep their IR order (not base-table order) for predictable columns
    for m in metrics:
        select_parts.append(f"agg_{m.table}.{qi(m.name)} AS {qi(m.name)}")

    sql = f"{with_block}\nSELECT {', '.join(select_parts)}\nFROM {first}"
    for name, _ in ctes[1:]:
        if key_cols:
            on = " AND ".join(
                f"{first}.{qi(k)} = {name}.{qi(k)}" for k, _ in key_cols
            )
            sql += f"\nJOIN {name} ON {on}"
        else:
            sql += f"\nCROSS JOIN {name}"

    order = _order_by(ir, qi)
    if order:
        sql += f"\nORDER BY {order}"
    if ir.limit is not None:
        sql += f"\n{dialect.limit_clause(ir.limit)}"

    return sql, params


# ----------------------------------------------------------------------------
# shared helpers
# ----------------------------------------------------------------------------
def _field_table(model: SemanticModel, field_name: str) -> str:
    return model.field(field_name).table


def _col_sql(model, d, dialect, qualify: bool = True) -> str:
    """SQL for a dimension/fact reference. A portable time derivation (`grain`/`part`
    over the source `column`) is compiled through the dialect; a raw `expr` is emitted
    verbatim (dialect-specific escape hatch); otherwise the physical column."""
    qi = dialect.quote_ident
    grain = getattr(d, "grain", None)
    part = getattr(d, "part", None)
    if grain or part:
        col = (f"{qi(model.table(d.table).table)}.{qi(d.column)}" if qualify
               else qi(d.column))
        return dialect.date_trunc(grain, col) if grain else dialect.date_part(part, col)
    expr = getattr(d, "expr", None)
    if expr:
        return f"({expr})"
    if qualify:
        return f"{qi(model.table(d.table).table)}.{qi(d.column)}"
    return qi(d.column)


def _where(ir, model, dialect, qualify: bool):
    """Build the WHERE for the single-base path (optionally table-qualified)."""
    qi = dialect.quote_ident
    clauses: list[str] = []
    params: list = []

    for f in ir.filters:
        d = model.field(f.field)
        clause, p = _filter_sql(_col_sql(model, d, dialect, qualify), f, dialect)
        clauses.append(clause)
        params += p

    if ir.time:
        d = model.dimension(ir.time.field)
        # derived-aware: a derived time dim (e.g. week_start) uses its expr as the LHS.
        colexpr = _col_sql(model, d, dialect, qualify)
        clauses.append(_time_clause(ir.time, colexpr, dialect))

    return " AND ".join(clauses), params


def _time_clause(t, colexpr, dialect) -> str:
    """Lower a TimeWindow to a two-sided **wall-clock** window.
    - `to_date`: the current `unit` so far — `[start of this unit, today]` (YTD/MTD),
      inclusive of today's partial period.
    - `trailing`: the last `t.last` COMPLETE `t.unit`s up to today, excluding the
      current partial one — `[cur - last units, cur)`, so "past month" (last=1) is
      exactly the previous calendar month. Either way a period with no data → no rows."""
    if t.kind == "to_date":
        start = dialect.date_trunc(t.unit, dialect.current_date())
        today = dialect.date_trunc("day", dialect.current_date())
        return f"{colexpr} >= {start} AND {colexpr} <= {today}"
    cur = dialect.date_trunc(t.unit, dialect.current_date())
    lower = dialect.relative_date(t.last, t.unit, cur)
    return f"{colexpr} >= {lower} AND {colexpr} < {cur}"


def resolve_window_sql(t, dialect) -> str:
    """SQL that resolves a relative `TimeWindow` to its concrete bucket boundaries so
    the UI can show which period was covered (e.g. "past month" -> Jun 2026, YTD ->
    "Jan 2026 - Jul 2026"). Wall-clock, matching `_time_clause`; `period_start`/
    `period_end` are the first/last bucket (equal for a single-bucket window)."""
    if t.kind == "to_date":
        start = dialect.date_trunc(t.unit, dialect.current_date())          # start of period
        end = dialect.date_trunc("month", dialect.current_date())           # current month
        return f"SELECT {start} AS period_start, {end} AS period_end"
    cur = dialect.date_trunc(t.unit, dialect.current_date())
    start = dialect.relative_date(t.last, t.unit, cur)   # first included bucket
    end = dialect.relative_date(1, t.unit, cur)          # last included bucket (cur - 1 unit)
    return f"SELECT {start} AS period_start, {end} AS period_end"


def _where_for_base(ir, model, dialect, base, key_cols):
    """WHERE for one base table's aggregate CTE in the multi-base path. A filter
    applies to this base if its column physically lives on the base."""
    qi = dialect.quote_ident
    base_cols = model.physical_columns(base)
    key_names = {name for name, _ in key_cols}
    clauses: list[str] = []
    params: list = []

    for f in ir.filters:
        d = model.field(f.field)
        if d.column not in base_cols and f.field not in key_names:
            continue
        clause, p = _filter_sql(qi(d.column), f, dialect)
        clauses.append(clause)
        params += p

    if ir.time:
        d = model.dimension(ir.time.field)
        if d.column in base_cols or ir.time.field in key_names:
            colexpr = _col_sql(model, d, dialect, qualify=False)  # grain/derived-aware
            clauses.append(_time_clause(ir.time, colexpr, dialect))

    return " AND ".join(clauses), params


def _filter_sql(colexpr: str, f: Filter, dialect: Dialect):
    ph = dialect.placeholder()
    if f.op in ("in", "not in"):
        values = list(f.value)
        if not values:
            raise CompileError(f"{f.op} filter on {f.field} needs a non-empty list")
        placeholders = ", ".join(ph for _ in values)
        keyword = "IN" if f.op == "in" else "NOT IN"
        return f"{colexpr} {keyword} ({placeholders})", values
    if f.op == "like":
        return f"{colexpr} LIKE {ph}", [f.value]
    return f"{colexpr} {f.op} {ph}", [f.value]


def _order_by(ir, qi) -> str:
    # order by the SELECT alias (metric or dimension logical name)
    return ", ".join(f"{qi(o.field)} {o.dir.upper()}" for o in ir.order_by)
