"""Evaluation dataset: (question, expected IR) cases loaded from YAML.

A case pairs a natural-language question with the Semantic Query IR a correct
planner should produce. The `expected` block is exactly the IR dict shape that
``SemanticQuery.from_dict`` consumes, so the dataset stays in lockstep with the
engine's IR.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml

from ..engine.ir import SemanticQuery


class DatasetError(Exception):
    pass


@dataclass
class EvalCase:
    id: str
    question: str
    expected: SemanticQuery
    # Alternative IRs that are *also* a correct answer (genuine intent
    # ambiguity, e.g. a scalar vs. grouping by the dimension you filtered on).
    # Execution accuracy passes if the prediction matches the primary expected
    # OR any of these; IR component scores are always measured against expected.
    also_accept: list[SemanticQuery] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)

    @property
    def acceptable(self) -> list[SemanticQuery]:
        return [self.expected, *self.also_accept]


def load_cases(path: str | Path) -> list[EvalCase]:
    """Load and parse evaluation cases. Raises DatasetError on a malformed case
    or a duplicate id, rather than silently skipping it."""
    data = yaml.safe_load(Path(path).read_text()) or {}
    raw_cases = data.get("cases")
    if not isinstance(raw_cases, list) or not raw_cases:
        raise DatasetError(f"{path}: expected a non-empty 'cases' list")

    cases: list[EvalCase] = []
    seen: set[str] = set()
    for i, raw in enumerate(raw_cases):
        case_id = raw.get("id")
        if not case_id:
            raise DatasetError(f"{path}: case #{i} is missing an 'id'")
        if case_id in seen:
            raise DatasetError(f"{path}: duplicate case id {case_id!r}")
        seen.add(case_id)
        if not raw.get("question"):
            raise DatasetError(f"{path}: case {case_id!r} is missing a 'question'")
        if "expected" not in raw:
            raise DatasetError(f"{path}: case {case_id!r} is missing 'expected'")
        try:
            expected = SemanticQuery.from_dict(raw["expected"])
            also_accept = [
                SemanticQuery.from_dict(alt) for alt in raw.get("also_accept", [])
            ]
        except Exception as e:  # noqa: BLE001 - re-raise with case context
            raise DatasetError(
                f"{path}: case {case_id!r} has an invalid expected IR: {e}"
            ) from e
        cases.append(
            EvalCase(
                id=case_id,
                question=raw["question"],
                expected=expected,
                also_accept=also_accept,
                tags=list(raw.get("tags", [])),
            )
        )
    return cases
