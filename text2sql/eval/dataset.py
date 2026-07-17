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


def _require_sql(value, path, case_id) -> str:
    if not isinstance(value, str) or not value.strip():
        raise DatasetError(f"{path}: case {case_id!r} has an empty expected_sql")
    return value


@dataclass
class EvalCase:
    id: str
    question: str
    # The reference answer: either a `SemanticQuery` (from an `expected` IR block)
    # or reference semantic SQL (a str, from `expected_sql`). The runner resolves a
    # str through the same `to_plan` the engine uses, so cases exercise the real
    # SQL front-end (last_period, HAVING, CASE pivots).
    expected: "SemanticQuery | str"
    # Alternative correct answers (genuine intent ambiguity). Execution accuracy
    # passes if the prediction matches the primary expected OR any of these.
    also_accept: list["SemanticQuery | str"] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)

    @property
    def acceptable(self) -> list["SemanticQuery | str"]:
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
        if "expected" not in raw and "expected_sql" not in raw:
            raise DatasetError(
                f"{path}: case {case_id!r} is missing 'expected' or 'expected_sql'"
            )
        try:
            if "expected_sql" in raw:
                expected = _require_sql(raw["expected_sql"], path, case_id)
            else:
                expected = SemanticQuery.from_dict(raw["expected"])
            also_accept = [
                alt if isinstance(alt, str) else SemanticQuery.from_dict(alt)
                for alt in raw.get("also_accept", [])
            ]
        except DatasetError:
            raise
        except Exception as e:  # noqa: BLE001 - re-raise with case context
            raise DatasetError(
                f"{path}: case {case_id!r} has an invalid expected: {e}"
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
