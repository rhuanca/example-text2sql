"""Semantic SQL front-end.

The planner (LLM) writes SQL against a single *virtual* table whose columns are
the model's dimensions and metrics (metrics are already-aggregated measures). We
parse that SQL (sqlglot), validate it against the model — this is the safety
boundary, replacing the old "the LLM can only emit a fixed IR" guarantee — and
normalize it into the existing `SemanticQuery` IR. The deterministic compiler
then resolves the real joins / fan-out and emits physical SQL.

Phase 1 supports: SELECT of bare metric/dimension columns, WHERE (a conjunction
of simple predicates + the `last_period(n, unit)` relative window), GROUP BY,
HAVING (on metrics), ORDER BY, LIMIT. No joins, subqueries, `SELECT *`, window
functions, or physical identifiers — each is rejected with a clear error the
engine's repair loop can surface. Literals become bound params in the compiler.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlglot import errors, exp, parse_one

from .compare import Comparison, compile_comparison, validate_comparison
from .compiler import compile as compile_ir
from .ir import Filter, OrderBy, SemanticQuery, TimeWindow
from .validator import validate_ir


class SemanticSqlError(Exception):
    pass


def _unaliased(sel):
    """The expression under a SELECT item, unwrapping an `AS` alias if present."""
    return sel.this if isinstance(sel, exp.Alias) else sel


@dataclass
class QueryShape:
    """The output shape of a window/derived query (which has no SemanticQuery
    form). Quacks like a SemanticQuery for chart selection and serialization:
    `.metrics` are the measure/derived output columns, `.dimensions` the rest."""

    metrics: list
    dimensions: list

    def to_dict(self) -> dict:
        return {"metrics": list(self.metrics), "dimensions": list(self.dimensions)}


def compile_semantic_sql(sql: str, model, dialect):
    """Full pipeline: LLM semantic SQL -> (physical_sql, params, plan). `plan` is a
    SemanticQuery, a Comparison (CASE pivot), or a QueryShape (window query); it is
    carried on the Result for chart selection. Raises SemanticSqlError / the
    engine's recoverable errors on anything invalid."""
    expr = _parse(sql)
    if expr.args.get("with") or expr.args.get("with_"):  # a single-CTE query
        return _lower_cte(expr, model, dialect)
    _basic_checks(expr, model)
    if _has_window(expr):
        dim_names = {d.name for d in model.dimensions}
        metric_names = {m.name for m in model.metrics}
        return _lower_window(expr, model, dialect, dim_names, metric_names)
    plan = _plan_from_expr(expr, model)
    if isinstance(plan, Comparison):
        validate_comparison(plan, model)
        physical, params = compile_comparison(plan, model, dialect)
    else:
        validate_ir(plan, model)
        physical, params = compile_ir(plan, model, dialect)
    return physical, params, plan


_CMP = {
    exp.EQ: "=", exp.NEQ: "!=", exp.LT: "<",
    exp.LTE: "<=", exp.GT: ">", exp.GTE: ">=",
}
_AGG = (exp.Sum, exp.Count, exp.Avg, exp.Min, exp.Max)


def to_plan(sql: str, model):
    """Parse + validate non-window semantic SQL into a plan: a period `Comparison`
    if it is a CASE-pivot, else a `SemanticQuery`. (Window queries go through
    compile_semantic_sql.) Raises SemanticSqlError on anything invalid."""
    expr = _parse(sql)
    _basic_checks(expr, model)
    return _plan_from_expr(expr, model)


def _plan_from_expr(expr, model):
    dim_names = {d.name for d in model.dimensions}
    metric_names = {m.name for m in model.metrics}
    pivot = _detect_pivot(expr, dim_names, metric_names)
    if pivot is not None:
        return pivot
    return _normalize(expr, model)


def parse_to_ir(sql: str, model) -> SemanticQuery:
    """Parse + validate plain semantic SQL into a SemanticQuery (no pivot)."""
    expr = _parse(sql)
    _basic_checks(expr, model)
    return _normalize(expr, model)


def _parse(sql: str):
    try:
        return parse_one(sql, read="sqlite")
    except errors.SqlglotError as e:
        raise SemanticSqlError(f"could not parse SQL: {e}")


def _basic_checks(expr, model) -> None:
    if not isinstance(expr, exp.Select):
        raise SemanticSqlError("only a single SELECT statement is allowed")
    if len(list(expr.find_all(exp.Select))) > 1 or list(expr.find_all(exp.Subquery)):
        raise SemanticSqlError("subqueries are not allowed")
    if list(expr.find_all(exp.Join)):
        raise SemanticSqlError("joins are not allowed; select from the one semantic table")
    _check_from(expr, model)


def _normalize(expr, model) -> SemanticQuery:
    dim_names = {d.name for d in model.dimensions}
    metric_names = {m.name for m in model.metrics}
    metrics, dimensions = _select_list(expr, dim_names, metric_names)
    filters, time = _where(expr, dim_names)
    having = _having(expr, metric_names)
    order_by = _order_by(expr, dim_names | metric_names)
    _check_group_by(expr, dim_names)
    limit = _limit(expr)
    if not metrics and not dimensions:
        raise SemanticSqlError("select at least one metric or dimension")
    return SemanticQuery(
        metrics=metrics, dimensions=dimensions, filters=filters,
        having=having, time=time, order_by=order_by, limit=limit,
    )


# ---- pivot detection (period comparison written as CASE) -------------------
def _detect_pivot(expr, dim_names, metric_names):
    """Recognize `SELECT <row_dim>, AGG(CASE WHEN <period> = <lit> THEN <metric>
    END) AS ..., ... GROUP BY <row_dim>` and return a Comparison. Any select item
    that isn't a bare row dimension or that exact CASE-aggregate shape means it is
    not a pivot -> return None (handled by the plain path)."""
    row_dims: list[str] = []
    cases: list[tuple[str, str, object]] = []  # (metric, period_field, value)
    for sel in expr.expressions:
        node = _unaliased(sel)
        if isinstance(node, exp.Column):
            if node.name not in dim_names:
                return None
            row_dims.append(node.name)
            continue
        parsed = _parse_case_agg(node, dim_names, metric_names)
        if parsed is None:
            return None
        cases.append(parsed)

    if len(cases) < 2 or not row_dims:
        return None
    metrics = {c[0] for c in cases}
    fields = {c[1] for c in cases}
    if len(metrics) != 1 or len(fields) != 1:
        return None  # a pivot is ONE metric across ONE period field

    # Fail loud on clauses a Comparison can't represent — never silently drop them.
    # (A window/filter IS carried below; ORDER BY is canonicalized to the row bucket.)
    if expr.args.get("having") is not None:
        raise SemanticSqlError("HAVING is not supported in a period comparison")
    if expr.args.get("limit") is not None:
        raise SemanticSqlError("LIMIT is not supported in a period comparison")

    filters, time = _where(expr, dim_names)  # carry the relative window, don't drop it
    return Comparison(
        metric=metrics.pop(),
        split_by=row_dims[0],
        period_field=fields.pop(),
        periods=[c[2] for c in cases],
        filters=filters,
        time=time,
    )


def _parse_case_agg(node, dim_names, metric_names):
    """AGG(CASE WHEN <period_dim> = <lit> THEN <metric> [ELSE ...] END) ->
    (metric, period_field, value), or None if it isn't that shape."""
    if not isinstance(node, _AGG):
        return None
    case = node.this
    if not isinstance(case, exp.Case):
        return None
    ifs = case.args.get("ifs") or []
    if len(ifs) != 1:
        return None
    cond, then = ifs[0].this, ifs[0].args.get("true")
    if not (isinstance(cond, exp.EQ) and isinstance(then, exp.Column)):
        return None
    period = _column_name(cond.this)
    metric = then.name
    if period not in dim_names or metric not in metric_names:
        return None
    return metric, period, _literal(cond.expression)


# ---- clause extractors -----------------------------------------------------
def _check_from(expr, model) -> None:
    frm = expr.find(exp.From)
    tables = list(frm.find_all(exp.Table)) if frm else []
    if len(tables) != 1 or tables[0].name != model.name:
        raise SemanticSqlError(
            f"FROM must be the semantic table {model.name!r}"
        )


def _select_list(expr, dim_names, metric_names):
    metrics: list[str] = []
    dimensions: list[str] = []
    for sel in expr.expressions:
        node = _unaliased(sel)
        if isinstance(node, exp.Star):
            raise SemanticSqlError("SELECT * is not allowed; name the metrics/dimensions")
        if not isinstance(node, exp.Column):
            raise SemanticSqlError(
                f"only metric/dimension columns may be selected, not: {node.sql()}"
            )
        name = node.name
        if name in metric_names:
            metrics.append(name)
        elif name in dim_names:
            dimensions.append(name)
        else:
            raise SemanticSqlError(f"unknown column: {name!r}")
    return metrics, dimensions


def _where(expr, dim_names):
    where = expr.args.get("where")
    filters: list[Filter] = []
    time: TimeWindow | None = None
    if not where:
        return filters, time
    for pred in _flatten_and(where.this):
        kind, value = _predicate(pred, dim_names)
        if kind == "time":
            if time is not None:
                raise SemanticSqlError("only one relative-time window is supported")
            time = value
        else:
            filters.append(value)
    return filters, time


def _having(expr, metric_names):
    having = expr.args.get("having")
    out: list[Filter] = []
    if not having:
        return out
    for pred in _flatten_and(having.this):
        op = _cmp_op(pred)
        if op is None:
            raise SemanticSqlError(f"unsupported HAVING predicate: {pred.sql()}")
        col = _column_name(pred.this)
        if col not in metric_names:
            raise SemanticSqlError(f"HAVING must reference a metric: {col!r}")
        out.append(Filter(col, op, _literal(pred.expression)))
    return out


def _order_by(expr, known_names):
    order = expr.args.get("order")
    out: list[OrderBy] = []
    if not order:
        return out
    for item in order.expressions:  # exp.Ordered
        col = _column_name(item.this)
        if col not in known_names:
            raise SemanticSqlError(f"ORDER BY references unknown column: {col!r}")
        out.append(OrderBy(col, "desc" if item.args.get("desc") else "asc"))
    return out


def _check_group_by(expr, dim_names) -> None:
    group = expr.args.get("group")
    if not group:
        return
    for g in group.expressions:
        col = _column_name(g)
        if col not in dim_names:
            raise SemanticSqlError(f"GROUP BY must be a dimension: {col!r}")


def _limit(expr):
    lim = expr.args.get("limit")
    if lim is None:
        return None
    return int(_literal(lim.expression))


# ---- predicate / literal helpers -------------------------------------------
def _predicate(pred, dim_names):
    """Return ("filter", Filter) or ("time", TimeWindow) for one WHERE conjunct."""
    op = _cmp_op(pred)
    if op is not None:
        col = _column_name(pred.this)
        right = pred.expression
        if isinstance(right, exp.Anonymous) and right.name.lower() == "last_period":
            if op not in (">=", ">"):
                raise SemanticSqlError("last_period(...) must be used with `>=` on a date")
            _require_dim(col, dim_names)
            n, unit = _last_period_args(right)
            return "time", TimeWindow(field=col, last=n, unit=unit)
        if isinstance(right, exp.Anonymous) and right.name.lower() == "period_to_date":
            if op not in (">=", ">"):
                raise SemanticSqlError("period_to_date(...) must be used with `>=` on a date")
            _require_dim(col, dim_names)
            unit = _to_date_args(right)
            return "time", TimeWindow(field=col, unit=unit, kind="to_date")
        _require_dim(col, dim_names)
        return "filter", Filter(col, op, _literal(right))

    if isinstance(pred, exp.In):
        col = _column_name(pred.this)
        _require_dim(col, dim_names)
        if pred.args.get("query"):
            raise SemanticSqlError("IN (subquery) is not allowed")
        return "filter", Filter(col, "in", [_literal(e) for e in pred.expressions])

    if isinstance(pred, exp.Like):
        col = _column_name(pred.this)
        _require_dim(col, dim_names)
        return "filter", Filter(col, "like", _literal(pred.expression))

    if isinstance(pred, exp.Not) and isinstance(pred.this, exp.In):
        inn = pred.this
        col = _column_name(inn.this)
        _require_dim(col, dim_names)
        return "filter", Filter(col, "not in", [_literal(e) for e in inn.expressions])

    raise SemanticSqlError(f"unsupported WHERE predicate: {pred.sql()}")


def _cmp_op(node):
    for cls, op in _CMP.items():
        if isinstance(node, cls):
            return op
    return None


def _flatten_and(cond):
    if isinstance(cond, exp.Paren):
        return _flatten_and(cond.this)
    if isinstance(cond, exp.And):
        return _flatten_and(cond.this) + _flatten_and(cond.expression)
    if isinstance(cond, exp.Or):
        raise SemanticSqlError("OR is not supported yet; use separate filters")
    return [cond]


def _column_name(node) -> str:
    if isinstance(node, exp.Paren):
        return _column_name(node.this)
    if isinstance(node, exp.Column):
        return node.name
    raise SemanticSqlError(f"expected a column, got: {node.sql()}")


def _require_dim(col, dim_names) -> None:
    if col not in dim_names:
        raise SemanticSqlError(f"unknown or non-filterable column: {col!r}")


def _literal(node):
    if isinstance(node, exp.Paren):
        return _literal(node.this)
    if isinstance(node, exp.Neg):
        return -_literal(node.this)
    if isinstance(node, exp.Boolean):
        return bool(node.this)
    if isinstance(node, exp.Null):
        return None
    if isinstance(node, exp.Literal):
        if node.is_string:
            return node.this
        try:
            return int(node.this)
        except ValueError:
            return float(node.this)
    raise SemanticSqlError(f"expected a literal value, got: {node.sql()}")


def _last_period_args(func):
    args = func.expressions
    if len(args) != 2:
        raise SemanticSqlError("last_period(n, unit) takes exactly two arguments")
    n = _literal(args[0])
    unit = _literal(args[1])
    if not isinstance(n, int) or n < 1:
        raise SemanticSqlError("last_period: first argument must be a positive integer")
    if unit not in ("day", "week", "month"):
        raise SemanticSqlError("last_period: unit must be 'day', 'week', or 'month'")
    return n, unit


def _to_date_args(func):
    args = func.expressions
    if len(args) != 1:
        raise SemanticSqlError("period_to_date(unit) takes exactly one argument")
    unit = _literal(args[0])
    if unit not in ("day", "week", "month", "quarter", "year"):
        raise SemanticSqlError(
            "period_to_date: unit must be 'day', 'week', 'month', 'quarter', or 'year'"
        )
    return unit


# ---- window / derived queries (e.g. period-over-period % change) ------------
def _has_window(expr) -> bool:
    return any(sel.find(exp.Window) for sel in expr.expressions)


def _lower_window(expr, model, dialect, dim_names, metric_names):
    """Lower a SELECT that uses window functions. The LLM writes it over the
    virtual table (e.g. `LAG(total_net_sales) OVER (ORDER BY iso_week)`); we
    compile a base aggregate (dimensions + referenced metrics, with WHERE/HAVING/
    window filter) via the ordinary compiler, then wrap the LLM's SELECT/ORDER/
    LIMIT over it as `(base) AS base`. So joins/fan-out stay deterministic and the
    window runs over already-aggregated rows. Returns (sql, params, QueryShape)."""
    # safety boundary: every referenced column must be a known dimension/metric
    for c in expr.find_all(exp.Column):
        if c.name not in dim_names and c.name not in metric_names:
            raise SemanticSqlError(f"unknown column: {c.name!r}")

    group = expr.args.get("group")
    if not group:
        raise SemanticSqlError("a window query must GROUP BY its dimensions")
    inner_dims = []
    for g in group.expressions:
        name = _column_name(g)
        if name not in dim_names:
            raise SemanticSqlError(f"GROUP BY must be a dimension: {name!r}")
        inner_dims.append(name)

    # dimensions/metrics used in the SELECT + ORDER BY must be produced by the
    # inner aggregate (WHERE columns are filters, handled separately below).
    scope = list(expr.expressions)
    order = expr.args.get("order")
    if order:
        scope += list(order.expressions)
    used_dims = {c.name for n in scope for c in n.find_all(exp.Column) if c.name in dim_names}
    missing = used_dims - set(inner_dims)
    if missing:
        raise SemanticSqlError(f"these dimensions must be in GROUP BY: {sorted(missing)}")

    inner_metrics = []
    for n in expr.expressions:
        for c in n.find_all(exp.Column):
            if c.name in metric_names and c.name not in inner_metrics:
                inner_metrics.append(c.name)
    if not inner_metrics:
        raise SemanticSqlError("a window query must reference at least one metric")

    filters, time = _where(expr, dim_names)
    inner = SemanticQuery(
        metrics=inner_metrics, dimensions=inner_dims, filters=filters,
        having=_having(expr, metric_names), time=time,
    )
    validate_ir(inner, model)
    inner_sql, params = compile_ir(inner, model, dialect)

    # outer = the LLM's SELECT/ORDER/LIMIT over the aggregated base subquery
    outer = expr.copy()
    for clause in ("where", "group", "having"):
        outer.set(clause, None)
    base = exp.Subquery(
        this=_parse(inner_sql),
        alias=exp.TableAlias(this=exp.to_identifier("base")),
    )
    outer.find(exp.From).set("this", base)  # swap the virtual table for the base subquery
    return outer.sql(dialect="sqlite"), params, _window_shape(expr, dim_names)


def _window_shape(expr, dim_names) -> QueryShape:
    metrics, dimensions = [], []
    for sel in expr.expressions:
        node = _unaliased(sel)
        if isinstance(node, exp.Column) and node.name in dim_names:
            dimensions.append(sel.alias_or_name)
        else:
            metrics.append(sel.alias_or_name)
    return QueryShape(metrics=metrics, dimensions=dimensions)


# ---- single CTE (multi-step: aggregate, then query the aggregate) -----------
def _lower_cte(expr, model, dialect):
    """Lower a single-CTE query: `WITH b AS (<semantic aggregate>) <outer over b>`.
    The CTE body is compiled with the ordinary compiler (so joins/fan-out stay
    deterministic); the outer is the LLM's SELECT over the CTE's OUTPUT columns only
    (e.g. a window / ranking / ratio over an aggregate). We emit a real `WITH` whose
    body is the compiled physical SQL. Returns (sql, params, QueryShape) — like the
    window path, the outer has no flat SemanticQuery form."""
    if not isinstance(expr, exp.Select):
        raise SemanticSqlError("only a SELECT may query a CTE")
    with_node = expr.args.get("with") or expr.args.get("with_")
    ctes = with_node.expressions
    if len(ctes) != 1:
        raise SemanticSqlError("only a single CTE (one WITH … AS …) is supported")
    cte = ctes[0]
    body = cte.this
    if not isinstance(body, exp.Select):
        raise SemanticSqlError("the CTE body must be a SELECT")

    # the CTE body must be a plain semantic aggregate (no window, no CASE pivot)
    dim_names = {d.name for d in model.dimensions}
    metric_names = {m.name for m in model.metrics}
    _basic_checks(body, model)
    if _has_window(body):
        raise SemanticSqlError("the CTE body may not use window functions")
    if _detect_pivot(body, dim_names, metric_names) is not None:
        raise SemanticSqlError("the CTE body may not be a CASE pivot")
    inner = _normalize(body, model)
    validate_ir(inner, model)
    inner_sql, params = compile_ir(inner, model, dialect)

    # scope = the CTE's output columns; the outer may reference only those
    cte_cols = {sel.alias_or_name for sel in body.expressions}
    _validate_cte_outer(expr, body, cte.alias, cte_cols)

    shape = _window_shape(expr, dim_names)  # outer SELECT list (before we mutate)
    cte.set("this", _parse(inner_sql))      # swap the body for its compiled SQL
    return expr.sql(dialect="sqlite"), params, shape


def _validate_cte_outer(expr, body, name, cte_cols) -> None:
    """The outer query must read only from the CTE and only its output columns —
    the safety boundary for the CTE case (no physical tables, no joins/subqueries)."""
    frm = expr.args.get("from") or expr.args.get("from_")
    tables = list(frm.find_all(exp.Table)) if frm else []
    if len(tables) != 1 or tables[0].name != name:
        raise SemanticSqlError(f"the outer query must select FROM the CTE {name!r}")
    if expr.find(exp.Join):  # the body has none (passed _basic_checks) -> any is outer
        raise SemanticSqlError("joins are not allowed in a CTE query")
    if list(expr.find_all(exp.Subquery)):
        raise SemanticSqlError("subqueries are not allowed in a CTE query")
    body_cols = {id(c) for c in body.find_all(exp.Column)}  # exclude the CTE body's own
    for col in expr.find_all(exp.Column):
        if id(col) not in body_cols and col.name not in cte_cols:
            raise SemanticSqlError(
                f"unknown column in the outer query: {col.name!r} — not produced by the CTE")
