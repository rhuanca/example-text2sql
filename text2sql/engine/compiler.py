"""Deterministic SQL compiler: Semantic Query IR -> (sql, params).

Pure function, no I/O and no LLM. Two paths:

* single base table  -> SELECT ... GROUP BY, with INNER JOINs to dimension
  tables (e.g. storeinfo) resolved from declared relationships.
* multiple base tables (metrics from 2+ tables, e.g. sales + budget)
  -> one aggregated subquery per base table grouped by the shared keys, then
  the aggregates are joined on those keys. This is the fan-out guard: a budget
  row is never joined to a raw sales line, so nothing double-counts.

Filter values are always emitted as bound parameters.
"""

from __future__ import annotations

from ..semantic.model import Dimension, Metric, SemanticModel
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
        return f"{qi(model.table(d.table).table)}.{qi(d.column)}"

    needed_tables = {d.table for d in dims}
    for f in ir.filters:
        needed_tables.add(_field_table(model, f.field))
    if ir.time:
        needed_tables.add(model.dimension(ir.time.field).table)
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

    order = _order_by(ir, qi)
    if order:
        sql += f"\nORDER BY {order}"
    if ir.limit is not None:
        sql += f"\n{dialect.limit_clause(ir.limit)}"

    return sql, params


def _join_clause(qi, model, base, other, rel):
    other_phys = qi(model.table(other).table)
    if rel.from_table == base:
        left = f"{qi(model.table(base).table)}.{qi(rel.from_column)}"
        right = f"{other_phys}.{qi(rel.to_column)}"
    else:
        left = f"{qi(model.table(base).table)}.{qi(rel.to_column)}"
        right = f"{other_phys}.{qi(rel.from_column)}"
    return f"JOIN {other_phys} ON {left} = {right}"


# ----------------------------------------------------------------------------
# multiple base tables (fan-out guard)
# ----------------------------------------------------------------------------
def _compile_multibase(ir, model, dialect, metrics, dims):
    qi = dialect.quote_ident
    params: list = []

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


def _where(ir, model, dialect, qualify: bool):
    """Build the WHERE for the single-base path (optionally table-qualified)."""
    qi = dialect.quote_ident
    clauses: list[str] = []
    params: list = []

    for f in ir.filters:
        d = model.field(f.field)
        if qualify:
            colexpr = f"{qi(model.table(d.table).table)}.{qi(d.column)}"
        else:
            colexpr = qi(d.column)
        clause, p = _filter_sql(colexpr, f, dialect)
        clauses.append(clause)
        params += p

    if ir.time:
        d = model.dimension(ir.time.field)
        colexpr = (
            f"{qi(model.table(d.table).table)}.{qi(d.column)}" if qualify else qi(d.column)
        )
        clauses.append(f"{colexpr} >= {dialect.relative_date(ir.time.last_n_days)}")

    return " AND ".join(clauses), params


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
            clauses.append(f"{qi(d.column)} >= {dialect.relative_date(ir.time.last_n_days)}")

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
