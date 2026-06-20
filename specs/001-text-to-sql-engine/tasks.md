# Tasks 001 — Text-to-SQL Engine

Spec: ./spec.md  Plan: ./plan.md
Each task is atomic, independently committable, and lands with its tests.

## T0 — Project scaffold
- `uv init`; add deps `pyyaml`, `anthropic`.
- Create package dirs `text2sql/`, `text2sql/engine/`,
  `text2sql/engine/dialects/`, `text2sql/semantic/`, `text2sql/db/`,
  `models/`, `tests/` with `__init__.py` where needed.
- Verify `uv run python -m unittest` runs (zero tests) green.
- DoD: repo importable; `uv run python -c "import text2sql"` works.

## T1 — Semantic model loader  (`semantic/model.py`, `tests/test_model.py`)
- Dataclasses: Table, Relationship, Dimension, Fact, Metric, Example,
  SemanticModel + lookup helpers.
- `load_model(path)` with structural validation (bad refs, dangling
  relationships, duplicate names).
- Tests: loads a valid inline model; raises on unknown metric table and on a
  relationship to a missing column.
- DoD: model tests pass.

## T2 — IR dataclasses  (`engine/ir.py`, `tests/test_ir.py`)
- Filter, TimeWindow, OrderBy, SemanticQuery + from_dict/to_dict + IR JSON
  schema constant.
- Tests: round-trip dict↔IR; schema lists the supported filter ops.
- DoD: IR tests pass.

## T3 — SQLite dialect + compiler core  (`engine/dialects/`, `engine/compiler.py`, `tests/test_compiler.py`)
- `Dialect` interface + `SqliteDialect`.
- `compile(ir, model, dialect) -> (sql, params)` for the single-base-table case:
  scalar aggregate, group-by, filters (all ops), order/limit, time sugar.
- Tests (no DB, no LLM): scalar metric; metric by dimension; each filter op;
  parameter binding with a quote in the value; time sugar expands to a date
  predicate.
- DoD: single-table compiler tests pass.

## T4 — Compiler joins + fan-out guard  (extends `compiler.py` + `test_compiler.py`)
- Cross-table: `sales` metric grouped by `storeinfo` dimension → join via
  relationship.
- Multi-base fan-out: `total_net_sales` + `total_budget` by `fc_number`(+date)
  → two aggregated CTEs joined on the keys.
- Tests: join uses the declared relationship; budget-vs-actual produces a
  two-aggregate join (assert no raw sales×budget join), correct grain.
- DoD: join + fan-out tests pass.

## T5 — Model + seed  (`models/sales.yml`, `text2sql/db/seed.py`)
- Write `models/sales.yml` (sales/storeinfo/budget per spec §5.1) with final
  column names.
- `seed.py` builds a fresh SQLite db with fixed sample rows (stores, products
  incl. Dozen Glazed, several ISO weeks incl. deleted/return rows, matching
  daily budget).
- DoD: `uv run python -m text2sql.db.seed` produces a db; `load_model` loads
  the YAML clean.

## T6 — Validator + Executor  (`engine/validator.py`, `engine/executor.py`, tests)
- Validator: SELECT/WITH-only, single statement, fields exist.
- `SqliteExecutor` read-only `run(sql, params)`.
- Tests: validator rejects non-SELECT and unknown field; executor returns
  expected non-empty rows for a compiled Dozen-Glazed weekly query against the
  seeded db.
- DoD: validator + executor tests pass.

## T7 — Engine + MockPlanner  (`engine/planner.py`, `engine/engine.py`, `tests/test_engine_e2e.py`)
- `Planner` protocol + `MockPlanner` (substring→canned IR).
- `Engine.ask` orchestration + bounded repair loop (max_retries=1).
- Tests: 2–3 canned questions end-to-end (incl. budget-vs-actual); a repair
  case where the first IR is bad and the retry succeeds.
- DoD: e2e tests pass; demo question returns rows + sql + ir.

## T8 — AnthropicPlanner  (`engine/planner.py`, gated smoke test)
- `AnthropicPlanner` builds prompt from model (dims/metrics/synonyms/
  sample_values/examples), constrains output to IR schema via a tool, parses to
  SemanticQuery, supports repair via `error` arg.
- Smoke test skipped unless `ANTHROPIC_API_KEY` is set.
- DoD: with a key, the demo question yields a valid IR; suite green without a key.

## T9 — Postgres dialect stub + seam test  (`engine/dialects/postgres.py`, test)
- `PostgresDialect` implementing the interface; placeholder/limit/quoting real,
  `relative_date` may raise NotImplementedError.
- Test: compiling a simple IR with the Postgres dialect produces `%s`
  placeholders / proper quoting (no compiler-core change needed).
- DoD: seam test passes.

## T10 — Wrap-up
- Top-level `README.md`: how to seed, run tests, ask a question (mock + real).
- Confirm full suite green offline; update spec §9 open items as resolved.
- DoD: clean `uv run python -m unittest`.

## Suggested commits
One per task (T0…T10). Branch first if requested; commit in small working
increments per global guidelines.
