# Tasks: Evaluation Harness (Spec 003)

Ordered, atomic, independently verifiable. Implement top to bottom; do not start
a task until the one above is green and committed.

- [x] E1. Dataset format + loader — `text2sql/eval/dataset.py` with `EvalCase`
  (id, question, tags, expected IR dict) and `load_cases(path)` that parses each
  `expected` via `SemanticQuery.from_dict` and raises a clear error on a bad
  case. — verify: `test_eval_scorer.py`/`test_eval_dataset` loads a small inline
  YAML and asserts typed cases; a malformed case raises.

- [x] E2. The dataset — `eval/cases.yml` with ~12–20 cases covering scalar
  aggregate, group-by, cross-table join, filter, time window, ordering, and the
  budget-vs-actual fan-out. — verify: a test loads `eval/cases.yml`, asserts
  every case id is unique and every `expected` parses as a valid IR.

- [x] E3. IR scorer — `text2sql/eval/scorer.py`: `score_ir(expected, predicted)`
  returning per-component precision/recall (metrics, dimensions, filters as
  sets) and an `exact` flag. — verify: `test_eval_scorer.py` covers
  order-insensitivity, partial credit, missing/extra components, and exact match.

- [x] E4. Result-set comparison — add `result_sets_match(exp_cols, exp_rows,
  pred_cols, pred_rows, ordered)` to `scorer.py`: column-set equality, numeric
  normalization (`5` == `5.0`), multiset rows unless `ordered`. — verify:
  `test_eval_scorer.py` cases for set vs ordered, numeric equality, and
  column/row mismatches.

- [x] E5. Runner + report — `text2sql/eval/runner.py` (`CaseResult`, `Report`,
  `run_suite(...)` that plans, scores IR, executes both IRs, compares, and
  captures per-case errors) and `text2sql/eval/report.py` (`format_report`). —
  verify: `test_eval_runner.py` runs `eval/cases.yml` with a `MockPlanner` whose
  rules satisfy the cases, asserts 100% execution accuracy and a well-formed
  report; one wrong rule yields a failed case with its error captured.

- [x] E6. CLI — `text2sql/eval/run.py`: `python -m text2sql.eval.run
  [--planner mock|anthropic] [--cases PATH]`, builds the model/dialect/executor
  (seeding the DB if needed) and prints `format_report`. — verify: run it with
  `--planner mock` and confirm it prints a per-case table + summary and exits 0.

- [x] E7. Docs + spec outcome — README "Evaluation" section and fill in
  `spec.md` §9 Outcome. — verify: README documents how to run the harness; full
  suite green (`uv run python -m unittest discover -s tests`).

## Done log
(Move completed tasks here with their commit hash.)
