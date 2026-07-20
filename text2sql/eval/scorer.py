"""Scoring: compare a predicted IR to an expected IR, and compare result sets.

Two views of correctness (see spec 003 §3):

* ``score_ir`` is a strict, *diagnostic* structural comparison. Metrics,
  dimensions, and filters are compared as sets (order is irrelevant for them),
  yielding per-component precision/recall plus an overall ``exact`` flag that
  also accounts for ordering (order_by), the time window, and the limit.
* ``result_sets_match`` is the *pass/fail* signal: do two queries return the
  same rows? It tolerates semantically-equivalent IRs that differ only in text.

Both are pure: no I/O, no LLM.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..engine.ir import SemanticQuery


def _hashable(value):
    """A stable, hashable key for a filter value (lists -> tuples)."""
    if isinstance(value, list):
        return ("__list__", tuple(_hashable(v) for v in value))
    return value


def _filter_keys(q: SemanticQuery) -> set:
    return {(f.field, f.op, _hashable(f.value)) for f in q.filters}


@dataclass
class ComponentScore:
    precision: float
    recall: float

    @property
    def f1(self) -> float:
        if self.precision + self.recall == 0:
            return 0.0
        return 2 * self.precision * self.recall / (self.precision + self.recall)


def _component(predicted: set, expected: set) -> ComponentScore:
    if not predicted and not expected:
        return ComponentScore(1.0, 1.0)
    tp = len(predicted & expected)
    # Empty predicted with a non-empty target = zero precision (nothing was
    # predicted right); empty target with non-empty predicted = perfect recall
    # (nothing to recall).
    precision = tp / len(predicted) if predicted else 0.0
    recall = tp / len(expected) if expected else 1.0
    return ComponentScore(precision, recall)


@dataclass
class IRScore:
    metrics: ComponentScore
    dimensions: ComponentScore
    filters: ComponentScore
    exact: bool


def _order_key(q: SemanticQuery):
    return [(o.field, o.dir) for o in q.order_by]


def _time_key(q: SemanticQuery):
    return None if q.time is None else (q.time.field, q.time.last, q.time.unit, q.time.kind)


def score_ir(expected: SemanticQuery, predicted: SemanticQuery) -> IRScore:
    """Per-component precision/recall plus an exact-match flag."""
    metrics = _component(set(predicted.metrics), set(expected.metrics))
    dimensions = _component(set(predicted.dimensions), set(expected.dimensions))
    filters = _component(_filter_keys(predicted), _filter_keys(expected))

    exact = (
        set(predicted.metrics) == set(expected.metrics)
        and set(predicted.dimensions) == set(expected.dimensions)
        and _filter_keys(predicted) == _filter_keys(expected)
        and _order_key(predicted) == _order_key(expected)
        and _time_key(predicted) == _time_key(expected)
        and predicted.limit == expected.limit
    )
    return IRScore(metrics=metrics, dimensions=dimensions, filters=filters, exact=exact)


# ---- result-set comparison (execution accuracy) ---------------------------
def _canon_cell(value):
    """Canonicalize a cell so 5 and 5.0 compare equal; everything else by str."""
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        f = float(value)
        return int(f) if f.is_integer() else f
    return value


def _canon_row(row, order: list[int]) -> tuple:
    return tuple(_canon_cell(row[i]) for i in order)


def result_sets_match(
    exp_cols: list[str],
    exp_rows: list,
    pred_cols: list[str],
    pred_rows: list,
    ordered: bool,
) -> bool:
    """True when two result sets are equal.

    Column *names* must match as a set (order-insensitive); cells are compared
    after numeric normalization. Rows are compared as a multiset unless
    ``ordered`` is set, in which case row order must match too. Predicted
    columns are reordered to the expected column order before comparing.
    """
    # Duplicate column names would make the name->index map ambiguous; reject
    # rather than silently compare the wrong columns.
    if len(exp_cols) != len(set(exp_cols)) or len(pred_cols) != len(set(pred_cols)):
        return False
    if set(exp_cols) != set(pred_cols):
        return False
    if len(exp_rows) != len(pred_rows):
        return False

    # Map each expected column to its index in the predicted columns.
    pred_index = {c: i for i, c in enumerate(pred_cols)}
    pred_to_exp_order = [pred_index[c] for c in exp_cols]

    exp_canon = [tuple(_canon_cell(v) for v in r) for r in exp_rows]
    pred_canon = [_canon_row(r, pred_to_exp_order) for r in pred_rows]

    if ordered:
        return exp_canon == pred_canon
    return sorted(exp_canon, key=repr) == sorted(pred_canon, key=repr)
