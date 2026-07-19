# Spec 001 — YAML-driven Text-to-SQL Engine

Status: Draft
Owner: rhuanca@gmail.com
Date: 2026-06-20

## 1. Problem statement

We want a text-to-SQL agent whose data model is defined in a **YAML semantic
model** rather than hard-coded. A user asks a natural-language question; the
engine produces SQL, executes it against a relational database, and returns
rows. The first database target is **SQLite** (zero-setup, seeded with sample
data); **Postgres** is the eventual production target.

This replaces a previous Snowflake-only solution (a Snowflake *semantic view*,
driven by Cortex Analyst) with a portable, open implementation we control. The
data model is based on a retail transaction schema with three tables: a
product-level sales fact table, a store dimension, and a daily budget fact.

This spec covers the **engine only**. A chat UI with plots is a later spec.

## 2. Goals / Non-goals

### Goals
- Define a YAML semantic-model format: tables, relationships, dimensions,
  facts, metrics, synonyms, sample values, and verified-query examples.
- Implement the engine as **NL → Semantic Query IR → deterministic SQL**.
  The LLM never writes raw SQL; it only selects metrics/dimensions/filters
  into a structured object. A deterministic, dialect-aware compiler emits SQL.
  > **Superseded by [ADR-0001](../decisions/0001-semantic-sql-over-fixed-ir.md) / [spec 005](../005-semantic-sql-frontend/spec.md).** The LLM
  > now authors semantic SQL over a *virtual* table, which is parsed and
  > validated against the model (the new safety boundary) before being normalized
  > to the same IR. Everything from the IR onward — the compiler, the fan-out
  > guard, dialects — is unchanged. Kept here as the original design record.
- Ship a concrete model for three tables: **fact_sales, dim_store, fact_budget**.
- Seed a SQLite database with small, deterministic sample data sufficient to
  answer real questions (e.g. "How is Dozen Glazed performing week over week?").
- Make the planner swappable: an Anthropic Claude planner and a mock planner.
- Test compiler/validator/executor without any LLM; test end-to-end with the
  mock planner.

### Non-goals (deferred)
- Chat UI and plotting — separate later spec.
- Postgres dialect *implementation* may be deferred, but the compiler must be
  structured so a Postgres dialect drops in without refactoring the IR.
- Embedding/retrieval for very large schemas — at three tables we pass the
  whole model to the planner. (Schema Linker exists but does cheap pruning.)
- The NetSuite flour/procurement table — out of iteration one.

## 3. Architecture

### Key decision
The LLM's only job is to translate the question into a **Semantic Query IR**
(which metrics, which group-by dimensions, which filters, time window, order,
limit). A deterministic compiler turns the IR into SQL. This mirrors the
Snowflake semantic view: Cortex Analyst picks `DIMENSIONS`/`METRICS`/`WHERE`
and Snowflake compiles to SQL. Benefits: the LLM can only reference things that
exist in the model; joins, aggregation grain, and fan-out are handled by code
that is pure and unit-testable; portability across dialects is one compiler
module per dialect on the same IR.

### Pipeline

```
   user question                  ┌──────────────── TEXT-TO-SQL ENGINE ────────────────┐
 "How is Cappuccino   ───────►  │                                                     │
  performing WoW?"                │  1. Schema Linker      (entity discovery)           │
                                  │       narrow model to relevant tables/dims/metrics, │
                                  │       resolve synonyms (revenue→total_net_sales)    │
                                  │                  │                                  │
                                  │                  ▼                                  │
                                  │  2. Query Planner      (intent discovery, LLM)      │
                                  │       NL ──► Semantic Query IR                       │
                                  │       (Anthropic Claude | Mock)                      │
                                  │                  │                                  │
                                  │                  ▼                                  │
                                  │  3. SQL Compiler       (deterministic, pure)        │
                                  │       IR ──► dialect SQL (SQLite | Postgres)         │
                                  │       joins via relationships, grain, fan-out guard  │
                                  │                  │                                  │
                                  │                  ▼                                  │
                                  │  4. Validator/Guardrails                            │
                                  │       SELECT-only, fields exist, optional EXPLAIN    │
                                  │                  │                                  │
                                  │                  ▼                                  │
                                  │  5. Executor ──────────────────► DB                 │
                                  │       returns columns + rows                        │
                                  │                  │                                  │
                                  │            error │ ok                               │
                                  │                  ▼                                  │
                                  │  6. Repair loop (bounded): error ─► back to step 2  │
                                  │                                                     │
                                  └─────────────────────────────────────────────────────┘
                                                     │
                                                     ▼
                                          columns + rows (+ the SQL, the IR)
```

### Component responsibilities
- **Schema Linker** (`engine/linker.py`): given the question and the full model,
  resolve synonyms and prune to a candidate set of tables/dimensions/metrics.
  At three tables this is cheap keyword/synonym matching; the interface is built
  so retrieval can replace it later. Output: a pruned model view handed to the
  planner.
- **Query Planner** (`engine/planner.py`): the only fuzzy step. `Planner`
  protocol with `plan(question, model_view) -> SemanticQuery`. Two
  implementations: `AnthropicPlanner` (calls the Claude API, uses tool/JSON
  output constrained to the IR schema, with verified-query examples as
  few-shot) and `MockPlanner` (deterministic, rule/lookup-based, for tests).
- **SQL Compiler** (`engine/compiler.py`): pure function
  `compile(ir, model, dialect) -> SQL`. Resolves which tables are needed from
  the referenced fields, builds joins from declared relationships, applies the
  metric aggregations and base filters, and avoids fan-out (see §6). No I/O, no
  LLM — fully unit-testable.
- **Validator** (`engine/validator.py`): rejects non-SELECT statements;
  confirms every referenced field exists in the model; optional `EXPLAIN`
  dry-run against the DB.
- **Executor** (`engine/executor.py`): runs the SQL, returns
  `(columns, rows)`. Read-only connection where the dialect supports it.
- **Engine** (`engine/engine.py`): orchestrates the pipeline and the bounded
  repair loop; returns a result object carrying rows, the SQL, and the IR.

## 4. Repository layout

```
text2sql/
  semantic/
    model.py            # dataclasses + YAML loader + structural validation
  engine/
    ir.py               # SemanticQuery IR dataclasses
    linker.py           # Schema Linker
    planner.py          # Planner protocol, AnthropicPlanner, MockPlanner
    compiler.py         # IR -> SQL (deterministic)
    dialects/
      base.py           # Dialect interface (quoting, date funcs, limit)
      sqlite.py
      postgres.py       # may start as a thin stub
    validator.py
    executor.py
    engine.py
  db/
    seed.py             # build sqlite file + insert sample rows
models/
  sales.yml             # the fact_sales/dim_store/fact_budget semantic model
specs/
  001-text-to-sql-engine/spec.md
tests/
  test_model.py
  test_compiler.py      # no LLM
  test_validator.py
  test_executor.py      # against seeded sqlite
  test_engine_e2e.py    # mock planner end-to-end
pyproject.toml          # managed by uv
```

## 5. Formats

### 5.1 YAML semantic model

Mapping from the Snowflake semantic view:

```
Snowflake SEMANTIC VIEW            ->  YAML semantic model
TABLES (... COMMENT ...)           ->  tables: {name, table, description}
RELATIONSHIPS (... REFERENCES ...) ->  relationships: {from, to, on}
FACTS                              ->  columns with role: fact
DIMENSIONS (+ WITH SYNONYMS)       ->  dimensions: {name, expr, synonyms}
METRICS (SUM/COUNT DISTINCT ...)   ->  metrics: {name, sql/agg, synonyms, filters}
AI_VERIFIED_QUERIES                ->  examples: planner few-shot
WITH EXTENSION (sample_values)     ->  sample_values: literal grounding
```

Sketch:

```yaml
name: product_sales
dialect: sqlite

tables:
  - name: fact_sales
    table: fact_sales
    grain: "one row per product line per transaction"
    description: "Combined in-store (NCR) and online (OLO) product-level detail."
  - name: dim_store
    table: dim_store
    primary_key: store_id
    description: "Store/franchise master dimension. One row per store."
  - name: fact_budget
    table: fact_budget
    grain: "one row per store per calendar day"
    description: "Planned daily net sales targets."

relationships:
  - from: fact_sales.store_id
    to: dim_store.store_id
  - from: fact_budget.store_id
    to: dim_store.store_id

dimensions:
  - { table: fact_sales, name: product_name, column: product_name,
      synonyms: [item, product, sku name] }
  - { table: fact_sales, name: category_name, column: category_name,
      synonyms: [category, product category] }
  - { table: fact_sales, name: purchase_location, column: purchase_location,
      synonyms: [channel, in-store vs online], sample_values: [At Shop, Online] }
  - { table: fact_sales, name: date, column: date, type: date,
      synonyms: [business date, transaction date, day] }
  - { table: fact_sales, name: iso_week, column: iso_week, synonyms: [week] }
  - { table: fact_sales, name: iso_year, column: iso_year, synonyms: [year] }
  - { table: fact_sales, name: store_id, column: store_id,
      synonyms: [store, location, fc] }
  - { table: dim_store, name: market, column: market, synonyms: [market, area] }
  - { table: dim_store, name: region, column: region, synonyms: [region] }
  - { table: dim_store, name: state, column: state }
  - { table: dim_store, name: corporate_franchise, column: corporate_franchise,
      synonyms: [ownership type, corp vs franchise] }
  - { table: dim_store, name: lifecycle_stage, column: lifecycle_stage,
      synonyms: [store status] }

facts:
  - { table: fact_sales, name: item_net_sales, column: item_net_sales }
  - { table: fact_sales, name: quantity, column: quantity }
  - { table: fact_budget, name: budget_net_sales, column: budget_net_sales }

metrics:
  - name: total_net_sales
    table: sales
    sql: "SUM(CASE WHEN transaction_deleted = 0 THEN item_net_sales ELSE 0 END)"
    synonyms: [sales, net sales, revenue, total sales]
  - name: units_sold
    table: sales
    sql: "SUM(CASE WHEN transaction_deleted = 0 THEN quantity ELSE 0 END)"
    synonyms: [units, quantity sold, volume]
  - name: traffic
    table: sales
    sql: "COUNT(DISTINCT CASE WHEN transaction_deleted = 0
           AND transaction_return = 0 THEN transaction_id END)"
    synonyms: [orders, checks, transactions, traffic]
  - name: total_budget
    table: budget
    sql: "SUM(budget_net_sales)"
    synonyms: [budget, budgeted sales, sales target, plan]

examples:
  - question: "How is Dozen Glazed performing week over week?"
    ir:
      metrics: [total_net_sales, units_sold]
      dimensions: [product_name, iso_year, iso_week]
      filters: [{ field: product_name, op: "=", value: "Dozen Glazed" }]
      order_by: [{ field: iso_week, dir: asc }]
```

### 5.2 Semantic Query IR

```yaml
metrics:    [total_net_sales, units_sold]      # metric names from the model
dimensions: [product_name, iso_year, iso_week] # group-by dimension names
filters:                                        # ANDed predicates
  - { field: product_name, op: "=", value: "Dozen Glazed" }
time:       { field: date, last_n_days: 42 }   # optional sugar -> a filter
order_by:   [{ field: iso_week, dir: asc }]
limit:      100
```

IR rules:
- `metrics` and `dimensions` reference names that must exist in the model.
- Supported filter ops (iteration one): `=`, `!=`, `<`, `<=`, `>`, `>=`,
  `in`, `not in`, `like`. Values are bound as parameters, never interpolated.
- `time` is sugar the compiler expands into a date filter; SQLite uses
  `date('now', ...)`, Postgres uses interval arithmetic (dialect-specific).
- A valid IR may have zero dimensions (a scalar aggregate) and one or more
  metrics, or be dimension-only (a detail listing) — both compile.

## 6. Key behaviors / acceptance criteria

1. **Model loads & validates.** `models/sales.yml` loads into typed objects;
   loading rejects unknown metric/dimension references and dangling
   relationships.
2. **Compiler is deterministic and pure.** Given an IR + model + dialect it
   returns the same SQL with no I/O. Covered by unit tests with no LLM.
3. **Joins from relationships.** When an IR mixes `sales` metrics with
   `storeinfo` dimensions (e.g. revenue by market), the compiler joins via the
   declared `sales.store_id -> storeinfo.store_id` relationship.
4. **Fan-out guard for budget vs actual.** Budget is daily per store; sales is
   per line. The compiler must aggregate budget and actuals **separately** and
   join on `store_id` (+ date grain) — never join raw tables, which would fan
   one budget row across every sales line and double count. A test asserts a
   budget-vs-actual IR produces a join of two aggregates (CTEs/subqueries),
   not a raw-table join.
5. **Parameter binding.** Filter values are passed as bound parameters; a value
   containing a quote does not break or inject SQL.
6. **Guardrails.** Validator rejects anything that is not a single SELECT and
   any field not present in the model.
7. **Executor returns data.** Against the seeded SQLite DB, a compiled
   "Dozen Glazed weekly sales" query returns the expected non-empty rows with
   correct column names.
8. **End-to-end with mock planner.** `engine.ask("How is Dozen Glazed
   performing week over week?")` with the `MockPlanner` returns rows, the SQL,
   and the IR.
9. **Repair loop bounded.** On a validation/execution error the engine re-plans
   with the error appended, at most N times (default 1 retry), then surfaces a
   clean error.
10. **Dialect seam.** SQLite is fully implemented; the Postgres dialect class
    exists with the interface defined, even if some methods raise
    `NotImplementedError`. No IR or compiler-core change is needed to add it.

## 7. Sample data (SQLite seed)

`db/seed.py` creates `fact_sales`, `dim_store`, `fact_budget` with a handful of stores
(e.g. ST001, ST002 across markets Denver/Portland), a few products including
`Cappuccino`, several ISO weeks of dated transactions (some flagged
deleted/return to exercise the metric base filters), and matching daily budget
rows. Data is small, fixed, and committed so tests are reproducible — no random
generation.

## 8. Testing strategy

- `unittest` only (per global guidelines).
- `test_model.py`: load valid model; reject bad references.
- `test_compiler.py`: IR → SQL snapshots for scalar aggregate, group-by,
  cross-table join, budget-vs-actual fan-out guard, time sugar, parameter
  binding. No DB, no LLM.
- `test_validator.py`: reject non-SELECT, reject unknown field.
- `test_executor.py`: run compiled SQL against the seeded SQLite, assert rows.
- `test_engine_e2e.py`: `MockPlanner` drives the full pipeline for 2–3 canned
  questions including a repair-loop case.
- The `AnthropicPlanner` is exercised by a thin smoke test gated on an API key
  being present (skipped otherwise), so the suite is green offline.

## 9. Open items — RESOLVED
- ~~Physical column names~~: derived from the reference view, lowercased;
  booleans stored as 0/1 in SQLite; declared in `db/seed.py` and `models/sales.yml`.
- ~~Repair-loop retry count~~: defaults to **1** (`Engine(max_retries=1)`).
- ~~`time` sugar location~~: **kept in the IR, expanded in the compiler** (the
  compiler turns it into a dialect-specific date predicate).

## 10. Outcome
Implemented as tasks T0–T10 (see `tasks.md`). 40 unit tests pass; the suite is
green offline (2 live Anthropic tests auto-skip without a key). The deterministic
core, the fan-out guard, the SQLite executor, the repair loop, the real Claude
planner, and the Postgres dialect seam are all in place. Deferred to later specs:
Postgres execution against a live database, and the chat UI with plots.
```
