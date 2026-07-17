"""Runner: drive each case through a planner, score it, and aggregate.

For every case we ask the planner for an IR, score it structurally against the
expected IR, then measure *execution accuracy* by compiling and running both the
expected and predicted IR against the seeded database and comparing result sets.
A planning/compilation/execution failure is captured on the case (passed=False),
never aborting the whole suite.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..engine.compare import Comparison, compile_comparison, validate_comparison
from ..engine.compiler import compile
from ..engine.ir import SemanticQuery
from ..engine.semantic_sql import to_plan
from ..engine.validator import validate_ir, validate_sql
from ..semantic.model import SemanticModel
from .dataset import EvalCase
from .scorer import IRScore, result_sets_match, score_ir


@dataclass
class CaseResult:
    id: str
    question: str
    passed: bool  # execution accuracy: predicted rows == expected rows
    exact_ir: bool
    ir_score: IRScore | None = None
    predicted: "SemanticQuery | Comparison | None" = None
    error: str | None = None


@dataclass
class Report:
    results: list[CaseResult] = field(default_factory=list)

    @property
    def total(self) -> int:
        return len(self.results)

    @property
    def n_passed(self) -> int:
        return sum(1 for r in self.results if r.passed)

    @property
    def exec_accuracy(self) -> float:
        return self.n_passed / self.total if self.total else 0.0

    @property
    def exact_ir_rate(self) -> float:
        # over IR-scored cases only (a CASE-pivot has no IR score)
        scored = [r for r in self.results if r.ir_score is not None]
        if not scored:
            return 0.0
        return sum(1 for r in scored if r.exact_ir) / len(scored)

    def _mean(self, pick) -> float:
        scored = [pick(r.ir_score) for r in self.results if r.ir_score]
        return sum(scored) / len(scored) if scored else 0.0

    @property
    def mean_metric_f1(self) -> float:
        return self._mean(lambda s: s.metrics.f1)

    @property
    def mean_dimension_f1(self) -> float:
        return self._mean(lambda s: s.dimensions.f1)

    @property
    def mean_filter_f1(self) -> float:
        return self._mean(lambda s: s.filters.f1)


def _resolve(spec, model):
    """A case's expected / a planner's prediction is either a plan object or
    semantic SQL (a str). Normalize a str through the engine's own to_plan."""
    return to_plan(spec, model) if isinstance(spec, str) else spec


def _run_plan(plan, model, dialect, executor):
    """Compile + run a plan (SemanticQuery or a CASE-pivot Comparison)."""
    if isinstance(plan, Comparison):
        validate_comparison(plan, model)
        sql, params = compile_comparison(plan, model, dialect)
    else:
        validate_ir(plan, model)
        sql, params = compile(plan, model, dialect)
    validate_sql(sql)
    return executor.run(sql, params)


def run_suite(
    cases: list[EvalCase],
    planner,
    model: SemanticModel,
    dialect,
    executor,
) -> Report:
    report = Report()
    for case in cases:
        ir_score = None
        predicted = None
        passed = False
        exact = False
        error = None
        try:
            predicted = _resolve(planner.plan(case.question, model), model)
            expected_plan = _resolve(case.expected, model)
            # IR component scores only apply when both sides are plain queries;
            # a CASE-pivot (Comparison) is scored by execution accuracy alone.
            if isinstance(expected_plan, SemanticQuery) and isinstance(
                predicted, SemanticQuery
            ):
                ir_score = score_ir(expected_plan, predicted)
                exact = ir_score.exact
            pred_cols, pred_rows = _run_plan(predicted, model, dialect, executor)
            # Pass if the prediction reproduces any acceptable reading.
            for spec in case.acceptable:
                candidate = _resolve(spec, model)
                cand_cols, cand_rows = _run_plan(candidate, model, dialect, executor)
                if result_sets_match(
                    cand_cols,
                    cand_rows,
                    pred_cols,
                    pred_rows,
                    ordered=bool(getattr(candidate, "order_by", None)),
                ):
                    passed = True
                    break
        except Exception as e:  # noqa: BLE001 - one bad case must not abort the suite
            error = f"{type(e).__name__}: {e}"
        report.results.append(
            CaseResult(
                id=case.id,
                question=case.question,
                passed=passed,
                exact_ir=exact,
                ir_score=ir_score,
                predicted=predicted,
                error=error,
            )
        )
    return report
