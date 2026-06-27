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

The pipeline: **question → Planner (LLM) → Semantic Query IR → Compiler → SQL → DB.**
The single most important invariant: **the LLM never emits SQL.** It only produces
a structured `SemanticQuery` (IR) that *picks* metrics/dimensions/filters that exist
in the YAML semantic model. Everything from the IR onward is pure, deterministic,
unit-tested code. This is why the model can't hallucinate columns or joins.

- `semantic/model.py` — loads + validates `models/sales.yml` into a typed `SemanticModel`
  (tables, metrics, dimensions, relationships). Add a metric/dimension by editing the
  YAML; no engine change needed.
- `engine/ir.py` — the `SemanticQuery` dataclass and its JSON schema (what the planner emits).
- `engine/compiler.py` — pure `compile(ir, model, dialect) -> (sql, params)`. Two paths:
  **single base table** (GROUP BY + INNER JOINs to dim tables), and **multi-base**
  (metrics from 2+ fact tables, e.g. sales + budget) which aggregates each base table
  in its own subquery *then* joins on shared keys. This is the **fan-out guard**:
  a budget row is never joined to raw sales lines, so nothing double-counts. Filter
  values are always bound parameters.
- `engine/dialects/` — `base` protocol + `sqlite` / `postgres`. Same IR, one Dialect per DB.
  SQLite is the live target; Postgres compiles but isn't executed against a live DB yet.
- `engine/validator.py` — guardrails: SELECT-only and fields-must-exist.
- `engine/planner.py` — `Planner` protocol and `AnthropicPlanner` (real LLM). Tests
  drive the engine with small inline stub planners instead of the real client.
- `engine/engine.py` — orchestrates plan→validate_ir→compile→validate_sql→execute with a
  **bounded repair loop**: on a recoverable error it re-plans, passing the prior error
  string back to the planner (`max_retries=1` by default).
- `chat/` — Streamlit app. Chart type is chosen **deterministically from query shape**
  (time dim → line, categorical dim → bar, scalar → number, else table); the LLM prose
  summary is additive and degrades gracefully if the call fails.
- `eval/` — the planner (NL→IR) is the only fuzzy step; this harness turns its quality
  into a number + regression gate. `eval/cases.yml` pairs questions with expected IR.
  Scored two ways: **execution accuracy** (compile + run both expected and predicted IR,
  compare result sets as a multiset — forgives semantically-equivalent IRs) and
  **IR component scores** (precision/recall over metrics/dimensions/filters, diagnostic).
  `--min-accuracy` makes the CLI exit non-zero below the floor.

## Conventions

- Python 3.13, `uv` for everything (`uv add`, `uv run`), stdlib `unittest` for tests.
- The compiler, validator, and eval scorer are pure (no I/O, no LLM) and must stay that
  way — that's what keeps them deterministically testable.
- Spec-driven: each feature has a `specs/NNN-*/` folder (spec, plan, tasks). Specs 001
  (engine), 002 (chat UI), 003 (eval harness) are done; live Postgres execution is future work.
</content>
</invoke>
