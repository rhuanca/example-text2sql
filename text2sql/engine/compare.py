"""Period comparison: a structured request the planner emits (never SQL) that a
deterministic template turns into a *wide* pivot — one column per compared period.

This is intentionally NOT part of the SemanticQuery IR or the semantic model. It
is an engine step: the LLM picks a metric, a row bucket, a period field, and the
period values; the compiler here aggregates *long* (reusing the normal compiler)
and pivots it into columns. So "compare revenue for Jan–Mar between 2025 and 2026"
becomes:

    month | total_amount_2025 | total_amount_2026

The base aggregate is produced by the ordinary compiler, so all metric/join/filter
logic lives in one place; this module only adds the outer CASE-pivot.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..semantic.model import SemanticModel
from .compiler import CompileError
from .compiler import compile as compile_ir
from .dialects.base import Dialect
from .ir import Filter, SemanticQuery


class CompareError(CompileError):
    pass


@dataclass
class Comparison:
    metric: str  # a metric name from the model
    split_by: str  # dimension for the rows (usually a time bucket, e.g. txn_month)
    period_field: str  # dimension whose values become the compared columns (e.g. txn_year)
    periods: list  # the period values, one column each, e.g. [2025, 2026]
    filters: list[Filter] = field(default_factory=list)

    @classmethod
    def from_dict(cls, d: dict) -> "Comparison":
        return cls(
            metric=d["metric"],
            split_by=d["split_by"],
            period_field=d["period_field"],
            periods=list(d.get("periods", [])),
            filters=[
                Filter(f["field"], f["op"], f.get("value"))
                for f in d.get("filters", [])
            ],
        )

    def to_dict(self) -> dict:
        out = {
            "metric": self.metric,
            "split_by": self.split_by,
            "period_field": self.period_field,
            "periods": list(self.periods),
        }
        if self.filters:
            out["filters"] = [
                {"field": f.field, "op": f.op, "value": f.value} for f in self.filters
            ]
        return out


def compile_comparison(cmp: Comparison, model: SemanticModel, dialect: Dialect):
    """Compile a Comparison into (sql, params): a wide pivot with one metric
    column per period. Reuses the normal compiler for the inner long aggregate."""
    if len(cmp.periods) < 2:
        raise CompareError("a comparison needs at least two periods")

    qi = dialect.quote_ident
    ph = dialect.placeholder()

    # Inner: metric grouped by (split_by, period_field), with the base filters.
    inner = SemanticQuery(
        metrics=[cmp.metric],
        dimensions=[cmp.split_by, cmp.period_field],
        filters=cmp.filters,
    )
    inner_sql, inner_params = compile_ir(inner, model, dialect)

    split = qi(cmp.split_by)
    period = qi(cmp.period_field)
    metric = qi(cmp.metric)

    # The period placeholders live in the SELECT (before the subquery), so their
    # bound values must come first, then the inner query's own params.
    select_parts = [f"base.{split} AS {split}"]
    period_params = []
    for p in cmp.periods:
        alias = qi(f"{cmp.metric}_{p}")
        select_parts.append(
            f"SUM(CASE WHEN base.{period} = {ph} THEN base.{metric} ELSE 0 END) AS {alias}"
        )
        period_params.append(p)
    params = period_params + inner_params

    sql = (
        f"SELECT {', '.join(select_parts)}\n"
        f"FROM (\n{inner_sql}\n) AS base\n"
        f"GROUP BY base.{split}\n"
        f"ORDER BY base.{split}"
    )
    return sql, params


def validate_comparison(cmp: Comparison, model: SemanticModel) -> None:
    """Guardrail: every field a Comparison references must exist in the model."""
    try:
        model.metric(cmp.metric)
    except KeyError:
        raise CompareError(f"unknown metric: {cmp.metric!r}")
    for name in (cmp.split_by, cmp.period_field):
        try:
            model.dimension(name)
        except KeyError:
            raise CompareError(f"unknown dimension: {name!r}")
    for f in cmp.filters:
        if not model.has_field(f.field):
            raise CompareError(f"unknown filter field: {f.field!r}")
    if len(cmp.periods) < 2:
        raise CompareError("a comparison needs at least two periods")


# JSON schema for the planner's emit_comparison tool (mirrors the IR filter shape).
COMPARISON_JSON_SCHEMA = {
    "type": "object",
    "properties": {
        "metric": {"type": "string"},
        "split_by": {"type": "string"},
        "period_field": {"type": "string"},
        "periods": {"type": "array", "items": {}, "minItems": 2},
        "filters": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "field": {"type": "string"},
                    "op": {"type": "string"},
                    "value": {},
                },
                "required": ["field", "op", "value"],
            },
        },
    },
    "required": ["metric", "split_by", "period_field", "periods"],
}
