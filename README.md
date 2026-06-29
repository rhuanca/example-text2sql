# text2sql — an example of the IR pattern for Text-to-SQL

This project is a small, complete **reference implementation of the
Intermediate Representation (IR) pattern** applied to text-to-SQL.

The one idea worth taking away:

> **The LLM never writes SQL.** It translates a natural-language question into a
> structured, validated **Intermediate Representation** — and then plain,
> deterministic code compiles that IR into SQL.

```
question ──► Planner (LLM) ──► Semantic Query (IR) ──► Compiler ──► SQL ──► DB
              the only            the contract          pure, deterministic code
              fuzzy step                                (from here on, no LLM)
```

Everything from the IR onward is pure, unit-tested code, so the model can't
hallucinate columns, invent joins, or emit unsafe SQL. The data model lives in
one **YAML semantic model** (mirroring a Snowflake semantic view); the LLM may
only *pick* metrics/dimensions/filters that exist there.

SQLite is the live target (zero-setup, seeded sample data); Postgres compiles
through the same IR but isn't executed against a live database yet.

---

## What is an IR?

An **Intermediate Representation** is a structured description of *what to
query* — the middle step between a question and SQL. The idea is borrowed from
compilers, which translate `source code → IR → machine code` so that
optimization and validation happen once, in the middle. Here it's
`question → IR → SQL`.

In this project the IR is a `SemanticQuery` — a small, fixed-shape object the
planner fills in. For the question *"How is Cappuccino performing week over
week?"* the LLM emits:

```json
{
  "metrics": ["total_sale", "units_sold"],
  "dimensions": ["product_name", "year", "week"],
  "filters": [{ "field": "product_name", "op": "=", "value": "Cappuccino" }],
  "order_by": [{ "field": "week", "dir": "asc" }]
}
```

The full shape (all optional unless noted):

| field | meaning |
|---|---|
| `metrics` *(required)* | what to measure — names of aggregations, e.g. `total_sale` |
| `dimensions` *(required)* | how to slice it — group-by attributes, e.g. `market`, `week` |
| `filters` | keep only some rows — `{field, op, value}`; `op ∈ = != < <= > >= in "not in" like` |
| `time` | a rolling window — `{field, last_n_days}` |
| `order_by` | sort the result — `{field, dir}`; `dir ∈ asc \| desc` |
| `limit` | cap the row count |

The planner is *forced* to return an object of exactly this shape (via a
constrained Anthropic tool call), so the output is always a valid IR — never
prose, never SQL.

## Why the IR pattern?

Letting an LLM write SQL directly is risky: it invents columns, picks wrong
joins (silently wrong numbers), and is non-deterministic. Constraining it to an
IR fixes all of that:

- **No hallucinated columns** — the IR can only name metrics/dimensions defined
  in the YAML model; a `validate_ir` step rejects anything else.
- **Deterministic** — the same IR always compiles to the same SQL; the LLM is
  the only fuzzy step, and it's isolated.
- **Safe** — filter values are always emitted as **bound parameters** (no
  injection), and a validator enforces **SELECT-only**.
- **Correct joins & aggregation** — handled by the compiler from declared
  relationships, including a **fan-out guard** (budget-vs-actual aggregates each
  fact table separately *then* joins, so budget rows never double-count against
  sales lines).
- **Portable** — one `Dialect` per database on the same IR.
- **Testable** — the compiler, validator, and eval scorer are pure functions
  with no I/O and no LLM, so they're deterministically unit-tested.

---

## Build & run

Requires **Python 3.13** and [`uv`](https://docs.astral.sh/uv/).

```bash
uv sync                                          # install dependencies
```

The compiler, validator, executor, and engine all run **fully offline**. Only
the live LLM planner (and the chat UI / real eval) need an Anthropic API key.

### Use the real LLM planner

Create a `.env` in the project root (gitignored — never commit it):

```
ANTHROPIC_API_KEY=sk-ant-...
# optional: ANTHROPIC_MODEL=claude-opus-4-8
```

### Seed the sample database

```bash
uv run python -m text2sql.db.seed                # writes ./demo.db
```

### Run the tests

```bash
uv run python -m unittest discover -s tests                  # full suite (offline)
uv run python -m unittest tests.test_compiler                # one module
```

The live Anthropic test auto-skips when no API key is present.

### Ask a question (Python)

```python
from text2sql.semantic.model import load_model
from text2sql.engine.dialects.sqlite import SqliteDialect
from text2sql.engine.engine import Engine
from text2sql.engine.executor import SqliteExecutor
from text2sql.engine.planner import AnthropicPlanner
from text2sql.db.seed import build_database

model = load_model("models/sales.yml")
db = build_database("demo.db")
engine = Engine(model, AnthropicPlanner(), SqliteDialect(), SqliteExecutor(db))

r = engine.ask("Budget vs actual by store")
print(r.ir.to_dict())   # the IR the LLM produced
print(r.sql)            # the SQL the compiler emitted
print(r.rows)           # the result
```

### Chat UI (with charts)

A Streamlit app sits on the engine: ask a question and get a written summary, an
auto-selected chart (time → line, category → bar, scalar → number, else table),
the data table, and an expander revealing the generated SQL and IR. Needs an API
key.

```bash
uv run streamlit run text2sql/chat/app.py
```

Try: *"How is Cappuccino performing last week?"*, *"What were total sales?"*.

### Evaluation harness

The planner (NL → IR) is the only fuzzy step; the eval harness turns its quality
into a number and a CI regression gate. A committed dataset (`eval/cases.yml`)
pairs questions with the IR a correct planner should produce.

```bash
uv run python -m text2sql.eval.run                       # measure the real LLM (needs key)
uv run python -m text2sql.eval.run --min-accuracy 0.8    # exit non-zero below the floor
```

Each case is scored two ways: **execution accuracy** (compile + run both the
expected and predicted IR, compare result sets as a multiset — forgives
semantically-equivalent IRs) and **IR component scores** (precision/recall over
metrics/dimensions/filters, diagnostic). CI runs the unit tests on every push,
plus the real-planner eval when an `ANTHROPIC_API_KEY` secret is configured.

---

## Architecture

```
text2sql/
  semantic/model.py     # loads + validates models/sales.yml into a typed SemanticModel
  engine/
    ir.py               # the SemanticQuery IR dataclass + its JSON schema
    planner.py          # Planner protocol + AnthropicPlanner (real LLM, forced tool call)
    compiler.py         # pure compile(ir, model, dialect) -> (sql, params)
    validator.py        # guardrails: SELECT-only, and every field must exist in the model
    dialects/           # base protocol + sqlite (live) / postgres (compiles only)
    executor.py         # read-only SQLite execution
    engine.py           # orchestration: plan → validate → compile → validate → execute
  db/seed.py            # deterministic SQLite sample data
  config.py             # loads .env (ANTHROPIC_API_KEY / ANTHROPIC_MODEL)
  chat/                 # Streamlit UI
  eval/                 # NL→IR evaluation harness
models/sales.yml        # the semantic model (the single source of truth)
tests/                  # unit + e2e (+ a gated live-LLM test)
```

The flow, in code:

1. **`planner.plan(question, model)`** — the LLM returns an IR via a forced
   `emit_query` tool call constrained to the IR JSON schema.
2. **`validate_ir(ir, model)`** — every metric/dimension/filter field must exist
   in the model.
3. **`compile(ir, model, dialect)`** — pure IR → `(sql, params)`. Two paths: a
   **single base table** (GROUP BY + INNER JOINs to dimension tables) and a
   **multi-base** path (metrics from 2+ fact tables) that aggregates each table
   in its own subquery before joining — the fan-out guard.
4. **`validate_sql(sql)`** — SELECT-only.
5. **execute** against SQLite.

`Engine.ask()` wires these together with a **bounded repair loop**: on a
recoverable error it re-plans once, feeding the prior error back to the planner.

## The semantic model (`models/sales.yml`)

One YAML file is the source of truth — the prompt, the validator, and the
compiler all read from it. It declares six building blocks (mirroring a
Snowflake semantic view):

| block | example |
|---|---|
| **tables** | `fact_sales`, `dim_store`, `fact_budget` |
| **relationships** | `fact_sales.store_id → dim_store.store_id` |
| **facts** (raw columns) | `item_net_sales`, `quantity`, `budget_net_sales` |
| **dimensions** (attributes) | `market`, `product_name`, `date`, `week`, … |
| **metrics** (aggregations) | `total_sale = SUM(...)`, `traffic = COUNT(DISTINCT ...)`, … |
| **examples** | few-shot question → IR pairs that prime the planner |

Metrics and dimensions also carry **synonyms** (so *"revenue"* resolves to
`total_sale`, *"territory"* to `market`) and **sample values**, which is
what lets the planner map free wording onto canonical names.

**Extend it by editing the YAML** — add a metric or dimension and the engine
needs no change. Add a database by adding a `Dialect`.

## Status

- Engine, Postgres compile seam, chat UI, and eval harness: **done**.
- Live Postgres *execution* against a real database is future work.

See `specs/` for the spec-driven history (each feature has a spec, plan, tasks).
</content>
