# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
uv sync                                               # install deps (Python 3.13)
uv run python -m unittest discover -s tests           # full test suite (offline)
uv run python -m unittest tests.test_compiler         # one test module
uv run python -m unittest tests.test_compiler.TestCompiler.test_name   # one test
uv run python -m text2sql.db.seed                     # write ./demo.db sample data
uv run streamlit run text2sql/chat/app.py             # chat UI
uv run python -m text2sql.eval.run --min-accuracy 0.8   # eval real LLM (needs key)
```

The unit suite is fully offline. The live Anthropic test (`tests/test_planner_anthropic.py`)
and the eval need `ANTHROPIC_API_KEY` (the live test auto-skips when it is absent).
CI (`.github/workflows/ci.yml`) runs the unit tests on every push, plus the
real-planner eval when the API-key secret exists.

To use the real planner locally, put `ANTHROPIC_API_KEY=sk-ant-...` (optionally
`ANTHROPIC_MODEL=...`) in a `.env` at the project root (gitignored, loaded by `text2sql/config.py`).

## Architecture

The pipeline: **question → Planner (LLM) → semantic SQL → parse+validate → Semantic
Query IR → Compiler → physical SQL → DB.** The LLM authors SQL over a single
*virtual* table whose columns are the model's dimensions and metrics (`emit_sql`).
That SQL is parsed (sqlglot) and **validated against the model** — the safety
boundary: only known columns/functions, no joins, no physical tables, SELECT-only.
It is then normalized into the `SemanticQuery` IR, and everything from the IR
onward is pure, deterministic, unit-tested code. So the model can pick only fields
that exist, and the *compiler* (not the LLM) resolves joins and the fan-out guard.
(This replaced the earlier "LLM emits a fixed IR, never SQL" design.) A CASE-pivot
SELECT (`SUM(CASE WHEN <period> = <v> THEN <metric> END)` per period) is detected
in `semantic_sql.to_plan` and lowered to a `Comparison` — so period comparisons
written as SQL render as grouped bars, reusing `engine/compare.py`. A SELECT with
**window functions** (e.g. `LAG(<metric>) OVER (ORDER BY <dim>)` for period-over-
period % change) is lowered by `compile_semantic_sql` via outer-wrapping: it
compiles a base aggregate (dimensions + referenced metrics) with the ordinary
compiler, then wraps the LLM's SELECT/ORDER/LIMIT over it as `(base) AS base`, so
joins/fan-out stay deterministic. Such a query has no `SemanticQuery` form, so the
Result carries a `QueryShape` (metrics/dimensions) for chart selection.

- `semantic/model.py` — loads + validates `models/sales.yml` into a typed `SemanticModel`
  (tables, metrics, dimensions, relationships). Add a metric/dimension by editing the
  YAML; no engine change needed.
- `engine/semantic_sql.py` — the semantic-SQL front-end: parse the LLM's SQL over the
  virtual table, **validate** it against the model (the safety boundary), and normalize
  it to a plan. Supports SELECT of metric/dimension columns, WHERE (+ the
  `last_period(n, unit)` relative window), GROUP BY, HAVING, ORDER BY, LIMIT, CASE
  pivots (→ a `Comparison`), and window functions (→ outer-wrapped SQL + a
  `QueryShape`). `compile_semantic_sql` is the engine's single entry point.
- `engine/ir.py` — the `SemanticQuery` dataclass (the internal normalized form). `time`
  is a data-anchored relative window (`last`/`unit`/`anchor`); `having` filters metrics.
- `engine/compiler.py` — pure `compile(ir, model, dialect) -> (sql, params)`. Two paths:
  **single base table** (GROUP BY + INNER JOINs to dim tables), and **multi-base**
  (metrics from 2+ fact tables, e.g. sales + budget) which aggregates each base table
  in its own subquery *then* joins on shared keys. This is the **fan-out guard**:
  a budget row is never joined to raw sales lines, so nothing double-counts. Filter
  values are always bound parameters.
- `engine/dialects/` — `base` protocol + `sqlite` / `postgres`. Same IR, one Dialect per DB.
  SQLite is the live target; Postgres compiles but isn't executed against a live DB yet.
- `engine/validator.py` — guardrails: SELECT-only and fields-must-exist.
- `engine/planner.py` — `Planner` protocol and `AnthropicPlanner` (real LLM): emits
  semantic SQL via a forced `emit_sql` tool; the system prompt describes the virtual
  table. Tests drive the engine with stub planners (which may return a `SemanticQuery`
  directly, bypassing the SQL front-end).
- `engine/engine.py` — orchestrates plan→parse/validate→compile→validate_sql→execute with a
  **bounded repair loop**: on a recoverable error it re-plans, passing the prior error
  string back to the planner (`max_retries=1` by default).
- `chat/` — Streamlit app. Chart type is chosen **deterministically from query shape**
  (time dim → line, categorical dim → bar, scalar → number, else table); the LLM prose
  summary is additive and degrades gracefully if the call fails.
- `eval/` — the planner (NL→semantic SQL→plan) is the only fuzzy step; this harness turns its
  quality into a number + regression gate. `eval/cases.yml` pairs a question with either an
  expected IR (`expected:`) or reference semantic SQL (`expected_sql:`); the runner resolves a
  predicted/expected SQL string through the engine's `to_plan`, so cases exercise the real SQL
  front-end (last_period, HAVING, CASE pivots). Scored two ways: **execution accuracy** (compile
  + run both expected and predicted, compare result sets as a multiset — forgives
  semantically-equivalent phrasings; `also_accept` lists alternative correct readings) and
  **IR component scores** (precision/recall over metrics/dimensions/filters — only for plain
  queries; a CASE-pivot Comparison is judged by execution accuracy alone).
  `--min-accuracy` makes the CLI exit non-zero below the floor.

## Conventions

- Python 3.13, `uv` for everything (`uv add`, `uv run`), stdlib `unittest` for tests.
- The compiler, validator, and eval scorer are pure (no I/O, no LLM) and must stay that
  way — that's what keeps them deterministically testable.
- Spec-driven: each feature has a `specs/NNN-*/` folder (spec, plan, tasks). Specs 001
  (engine), 002 (chat UI), 003 (eval harness) are done; live Postgres execution is future work.
</content>
</invoke>
