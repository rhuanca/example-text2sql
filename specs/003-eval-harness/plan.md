# Plan: Evaluation Harness (Spec 003)

Derived from `spec.md`. Simplest design that satisfies the spec; reuses the
existing engine pieces unchanged.

## Architecture

A thin, pure-where-possible layer on top of the engine. A YAML dataset of
(question, expected IR) cases is loaded into typed objects. For each case the
runner asks a `Planner` for a predicted IR, then scores it two ways:

1. **IR component score** — pure comparison of predicted vs expected IR, with
   metrics/dimensions/filters treated as sets (order-insensitive), yielding
   precision/recall per component and an exact-match flag.
2. **Execution accuracy** — compile both IRs with the existing `compile()` and
   run them with the existing executor against the seeded SQLite, then compare
   result sets. This is the pass/fail signal.

Results aggregate into a `Report` that a formatter renders as text, and a CLI
drives the whole thing.

Nothing in `text2sql/engine/` changes. The harness only *consumes* the planner,
compiler, validator, and executor that already exist.

## Stack
- Language / runtime: Python + uv (existing project).
- Key libraries: stdlib + `pyyaml` (already a dep for the semantic model). No
  new dependencies.
- Testing: stdlib `unittest`.

## Layers
- `eval/cases.yml` — the committed dataset (data, not code).
- `text2sql/eval/dataset.py` — `EvalCase` dataclass + `load_cases(path)`.
- `text2sql/eval/scorer.py` — pure functions: `score_ir(expected, predicted)`
  (component P/R + exact flag) and `result_sets_match(...)` (row-set comparison
  with numeric normalization and order rules).
- `text2sql/eval/runner.py` — `CaseResult`, `Report`, `run_suite(cases, planner,
  model, dialect, executor)`; orchestrates plan → score → execute → aggregate,
  capturing per-case errors.
- `text2sql/eval/report.py` — `format_report(report) -> str`.
- `text2sql/eval/run.py` — CLI `python -m text2sql.eval.run [--planner mock|anthropic]`.

No layer is added "for the future": each maps directly to an acceptance
criterion. Execution accuracy reuses the engine's own compile/execute path
rather than re-implementing SQL generation.

## Data and config
- Dataset path defaults to `eval/cases.yml`, overridable via a CLI flag (no
  hardcoded absolute paths; resolved relative to the repo root like the existing
  `models/sales.yml`).
- The Anthropic planner reuses the existing `config.py` (API key / model from the
  environment). No secrets in the dataset or report.
- The seeded SQLite DB is built on demand (reusing `db/seed.build_database`) into
  a temp/`demo.db` path, exactly as the chat app does.

## Risks / unknowns
- **Semantically-equivalent IRs.** Mitigated by making execution accuracy the
  pass/fail signal; component scores are diagnostic only.
- **Row-set comparison edge cases** (float formatting, NULLs, ordering). Handled
  by canonicalizing cells (numeric coercion) and only enforcing row order when
  the expected IR specifies `order_by`.
- **Mock-planner circularity.** The offline regression test proves the *harness*
  works (scoring, execution, aggregation, error capture), not that the LLM is
  good. Real-planner accuracy is measured via the gated CLI/test when a key is
  present. This is acknowledged, not hidden.
