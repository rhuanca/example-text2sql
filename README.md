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
models/sales.yml           # sales / storeinfo / budget semantic model
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
rules = [("dozen glazed", {
    "metrics": ["total_net_sales", "units_sold"],
    "dimensions": ["iso_year", "iso_week"],
    "filters": [{"field": "product_name", "op": "=", "value": "Dozen Glazed"}],
    "order_by": [{"field": "iso_week", "dir": "asc"}],
})]
engine = Engine(model, MockPlanner(rules), SqliteDialect(), SqliteExecutor(db))
r = engine.ask("How is Dozen Glazed performing week over week?")
print(r.sql); print(r.rows)
```

With the real Claude planner (needs the key), swap the planner:

```python
from text2sql.engine.planner import AnthropicPlanner
engine = Engine(model, AnthropicPlanner(), SqliteDialect(), SqliteExecutor(db))
r = engine.ask("Budget vs actual by store")
print(r.ir.to_dict()); print(r.sql); print(r.rows)
```

## The semantic model (`models/sales.yml`)

Three tables, mapped from an existing Snowflake semantic view:

| logical table | source view | key fields |
|---|---|---|
| `sales` | `VW_NCR_OLO_TRANSACTION_LEVEL_DETAIL` | metrics `total_net_sales`, `units_sold`, `traffic` |
| `storeinfo` | `VW_FRANCONNECT_PROFILES` | dims `market`, `region`, `corporate_franchise`, … |
| `budget` | `VW_SWS_BUDGET` | metric `total_budget` |

Relationships: `sales.fc_number → storeinfo.fc_number`,
`budget.fc_number → storeinfo.fc_number`.

Add a metric/dimension by editing the YAML — no engine change needed. Add a
database by adding a `Dialect`.

## Status

- Engine (T0–T8), Postgres seam (T9), docs (T10): **done**.
- Postgres *execution* and a chat UI with plots are future specs.
