# text2sql — YAML-driven Text-to-SQL engine

A text-to-SQL agent whose data model lives in a **YAML semantic model**. A
question is translated by an LLM into a structured **Semantic Query (IR)** —
never raw SQL — and a deterministic, dialect-aware compiler turns that IR into
SQL. This mirrors a Snowflake semantic view: the model only *picks*
metrics/dimensions/filters; your code emits the SQL.

```
question ──► Schema/Planner (LLM) ──► Semantic Query IR ──► Compiler ──► SQL ──► DB
                                          (deterministic from here on)
```

Targets Postgres; **SQLite** is used first (zero-setup, seeded sample data).

## Why this shape

The LLM can only reference metrics/dimensions that exist in the model, so it
can't hallucinate columns. Joins, aggregation grain, and **fan-out avoidance**
(budget-vs-actual aggregates-then-joins, never raw-joins) are handled by pure,
unit-tested code. Portability is one `Dialect` per database on the same IR.

## Layout

```
text2sql/
  semantic/model.py        # YAML loader + validation, typed model
  engine/
    ir.py                  # SemanticQuery IR + JSON schema
    compiler.py            # pure IR -> SQL (single-base + multi-base fan-out guard)
    dialects/              # base / sqlite / postgres
    validator.py           # SELECT-only + fields-exist guardrails
    executor.py            # read-only SQLite execution
    planner.py             # Planner protocol, MockPlanner, AnthropicPlanner
    engine.py              # orchestration + bounded repair loop
  db/seed.py               # deterministic SQLite sample data
  config.py                # loads dotenv: ANTHROPIC_API_KEY / ANTHROPIC_MODEL
models/sales.yml           # fact_sales / dim_store / fact_budget semantic model
tests/                     # unit + e2e (+ gated live LLM test)
specs/001-text-to-sql-engine/   # spec, plan, tasks
```

## Setup

```bash
uv sync
```

To use the real LLM planner, create a `.env` in the project root:

```
ANTHROPIC_API_KEY=sk-ant-...
# optional: ANTHROPIC_MODEL=claude-opus-4-8
```

(`.env` is gitignored — never commit it. Everything except the live planner
works without a key.)

## Seed the database

```bash
uv run python -m text2sql.db.seed      # writes ./demo.db
```

## Run the tests

```bash
uv run python -m unittest discover -s tests
```

The compiler/validator/executor/engine suite runs fully offline. The live
Anthropic test is automatically **skipped** when no API key is present.

## Ask a question

Offline, with the deterministic mock planner:

```python
from text2sql.semantic.model import load_model
from text2sql.engine.dialects.sqlite import SqliteDialect
from text2sql.engine.engine import Engine
from text2sql.engine.executor import SqliteExecutor
from text2sql.engine.planner import MockPlanner
from text2sql.db.seed import build_database

model = load_model("models/sales.yml")
db = build_database("demo.db")
rules = [("cappuccino", {
    "metrics": ["total_net_sales", "units_sold"],
    "dimensions": ["product_name", "iso_year", "iso_week"],
    "filters": [{"field": "product_name", "op": "=", "value": "Cappuccino"}],
    "order_by": [{"field": "iso_week", "dir": "asc"}],
})]
engine = Engine(model, MockPlanner(rules), SqliteDialect(), SqliteExecutor(db))
r = engine.ask("How is Cappuccino performing week over week?")
print(r.sql); print(r.rows)
```

With the real Claude planner (needs the key), swap the planner:

```python
from text2sql.engine.planner import AnthropicPlanner
engine = Engine(model, AnthropicPlanner(), SqliteDialect(), SqliteExecutor(db))
r = engine.ask("Budget vs actual by store")
print(r.ir.to_dict()); print(r.sql); print(r.rows)
```

## Chat UI (with plots)

A Streamlit chat app sits on top of the engine: type a question and get a
written answer, an auto-selected chart, the data table, and the generated SQL/IR.

```bash
uv run streamlit run text2sql/chat/app.py
```

For each answer it shows, in order:
1. an **LLM prose summary** of the result,
2. a chart picked **deterministically from the query shape** — a time dimension
   (e.g. ISO week) → line, a categorical dimension (e.g. market) → bar, a scalar
   metric → a single number, otherwise just the table,
3. the **data table**, and
4. an expander revealing the **SQL and the Semantic Query (IR)**.

The summary is additive — if the LLM call fails the chart and table still show.
With no API key set, the app falls back to the deterministic mock planner so the
example questions still work. Try:

- *How is Cappuccino performing week over week?* → line chart over ISO week
- *What were total net sales by market?* → bar chart
- *Budget vs actual by store* → table (fan-out-safe)

## Evaluation harness

The planner is the only fuzzy step (NL → IR). The eval harness turns its quality
into a number and a regression gate. A committed dataset pairs questions with the
IR a correct planner should produce; a runner scores a planner against it.

```bash
uv run python -m text2sql.eval.run                   # mock planner — harness self-check (100%)
uv run python -m text2sql.eval.run --planner anthropic   # measure the real LLM (needs key)
uv run python -m text2sql.eval.run --cases eval/cases.yml
```

Each case is scored two ways:

1. **Execution accuracy** (pass/fail) — both the expected and predicted IR are
   compiled and run against the seeded SQLite, and the result sets are compared.
   This forgives semantically-equivalent IRs that differ only in text. Rows match
   as a multiset (numeric `5` == `5.0`), and order is enforced only when the
   expected IR specifies an `order_by`.
2. **IR component scores** (diagnostic) — precision/recall over metrics,
   dimensions, and filters (compared as sets), plus an `exact` flag that also
   accounts for ordering, the time window, and the limit. These pinpoint *which*
   part of a wrong query the planner got wrong.

`--min-accuracy 0.8` makes the CLI exit non-zero when execution accuracy drops
below the floor, so it doubles as a regression gate. GitHub Actions
(`.github/workflows/ci.yml`) runs the unit tests and the mock-planner eval on
every push/PR, and the real-planner eval when an `ANTHROPIC_API_KEY` secret is
configured.

The dataset lives in `eval/cases.yml`; add a case by writing a question and its
expected IR (same shape as the model's `examples`). The scorer and runner are
pure and covered by `tests/test_eval_scorer.py` / `tests/test_eval_runner.py`;
the mock-planner run keeps the suite green offline.

```
text2sql/eval/
  dataset.py   # EvalCase + load_cases
  scorer.py    # pure IR comparison + result-set comparison
  runner.py    # run_suite -> Report (per-case + summary)
  report.py    # text report
  run.py       # CLI
eval/cases.yml # the committed dataset
```

## The semantic model (`models/sales.yml`)

Three tables, mapped from an existing Snowflake semantic view:

| logical table | source view | key fields |
|---|---|---|
| `fact_sales` | `VW_NCR_OLO_TRANSACTION_LEVEL_DETAIL` | metrics `total_net_sales`, `units_sold`, `traffic` |
| `dim_store` | `VW_FRANCONNECT_PROFILES` | dims `market`, `region`, `corporate_franchise`, … |
| `fact_budget` | `VW_SWS_BUDGET` | metric `total_budget` |

Relationships: `fact_sales.store_id → dim_store.store_id`,
`fact_budget.store_id → dim_store.store_id`.

Add a metric/dimension by editing the YAML — no engine change needed. Add a
database by adding a `Dialect`.

## Status

- Spec 001 — engine (T0–T8), Postgres seam (T9), docs (T10): **done**.
- Spec 002 — chat UI with plots (U1–U5): **done**.
- Spec 003 — evaluation harness (E1–E7): **done**.
- Postgres *execution* against a live database is a future spec.
