# ADR 0005 — Evals are the regression contract (don't edit to pass)

Status: Accepted   ·   Date: 2026-07-19

## Context
The eval suite (`eval/cases.yml`, `eval/cases_qbo.yml`, the harness) scores planner output by
execution accuracy per semantic model, with a `--min-accuracy` CI gate and a committed quality
trend (spec 009). It is tempting to adjust an eval case when a run regresses.

## Decision
**Treat evals like unit tests: a fixed contract, not something to edit to make a run pass.** Do
not modify anything under `eval/` — cases or harness — or add/remove cases without an explicit
rationale and sign-off. When new behavior needs coverage, add regular unit tests under `tests/`
(that is free). When an eval regresses, investigate the code first — never adjust the eval to
match the new (possibly wrong) output.

## Alternatives considered
- **Treat eval cases as freely editable fixtures.** Faster green runs, but it hides regressions
  and defeats the purpose of a quality gate.

## Consequences
- The eval score/trend stays a trustworthy signal of whether the product is improving.
- New feature coverage lands in `tests/`; eval changes are deliberate and reviewed.
