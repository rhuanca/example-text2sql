"""Guardrails: enforce read-only SQL and that the IR only references fields
that exist in the semantic model."""

from __future__ import annotations

import re

from ..semantic.model import SemanticModel
from .ir import SemanticQuery

_FORBIDDEN = re.compile(
    r"\b(insert|update|delete|drop|alter|create|attach|pragma|replace|truncate|grant|vacuum)\b",
    re.IGNORECASE,
)


class ValidationError(Exception):
    pass


def validate_ir(ir: SemanticQuery, model: SemanticModel) -> None:
    for m in ir.metrics:
        if not _is_metric(model, m):
            raise ValidationError(f"unknown metric: {m!r}")
    for d in ir.dimensions:
        if not _is_dimension(model, d):
            raise ValidationError(f"unknown dimension: {d!r}")
    for f in ir.filters:
        if not model.has_field(f.field):
            raise ValidationError(f"unknown filter field: {f.field!r}")
    if ir.time and not _is_dimension(model, ir.time.field):
        raise ValidationError(f"unknown time field: {ir.time.field!r}")
    selectable = set(ir.metrics) | set(ir.dimensions)
    for o in ir.order_by:
        if o.field not in selectable:
            raise ValidationError(
                f"order_by field {o.field!r} is not selected by the query"
            )


def validate_sql(sql: str) -> None:
    stripped = sql.strip().rstrip(";")
    if ";" in stripped:
        raise ValidationError("multiple statements are not allowed")
    head = stripped.lstrip().lower()
    if not (head.startswith("select") or head.startswith("with")):
        raise ValidationError("only SELECT/WITH statements are allowed")
    if _FORBIDDEN.search(stripped):
        raise ValidationError("statement contains a forbidden keyword")


def _is_metric(model: SemanticModel, name: str) -> bool:
    try:
        model.metric(name)
        return True
    except KeyError:
        return False


def _is_dimension(model: SemanticModel, name: str) -> bool:
    try:
        model.dimension(name)
        return True
    except KeyError:
        return False
