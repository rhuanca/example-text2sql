# Plan 001 — Text-to-SQL Engine (technical plan)

Status: Draft
Spec: ./spec.md
Date: 2026-06-20

Decisions carried from spec (defaults accepted): booleans stored as 0/1 in
SQLite; repair-loop = 1 retry; first demo question = "How is Dozen Glazed
performing week over week?".

## 1. Stack & conventions
- Python 3.11+, dependency + execution via `uv`.
- Tests: stdlib `unittest`.
- Dependencies: `pyyaml` (model loading), `anthropic` (planner). SQLite via
  the stdlib `sqlite3` — no driver dep. Postgres driver deferred.
- Style: simplest thing that works; earned abstractions only. Dataclasses for
  the model and IR; a `Protocol` for the planner and the dialect seam.

## 2. Module-by-module design

### 2.1 `semantic/model.py`
Dataclasses: `Table`, `Relationship`, `Dimension`, `Fact`, `Metric`,
`Example`, `SemanticModel`. A `load_model(path) -> SemanticModel` reads YAML
and runs structural validation:
- every `relationship.from/to` references an existing `table.column`;
- every metric/dimension `table` exists;
- names are unique within their kind.
Helper lookups: `model.metric(name)`, `model.dimension(name)`,
`model.field(name)` (dimension or metric), `model.table_of(field)`.

### 2.2 `engine/ir.py`
Dataclasses: `Filter(field, op, value)`, `TimeWindow(field, last_n_days)`,
`OrderBy(field, dir)`, `SemanticQuery(metrics, dimensions, filters, time,
order_by, limit)`. Pure data; plus `SemanticQuery.from_dict` /
`to_dict` for planner round-tripping and a JSON schema constant the
`AnthropicPlanner` uses to constrain output.

### 2.3 `engine/dialects/`
`base.Dialect` interface: `quote_ident`, `quote_table`, `placeholder(i)`,
`relative_date(field_sql, last_n_days)`, `limit_clause(n)`. `sqlite.SqliteDialect`
implements all; `postgres.PostgresDialect` defines the class with the same
methods, `relative_date` etc. may raise `NotImplementedError` initially.

### 2.4 `engine/compiler.py`
`compile(ir, model, dialect) -> (sql, params)`. Pure.
Algorithm:
1. Collect referenced fields (metrics + dimensions + filter/order/time fields).
2. Determine required tables; pick the driving/base table (the table owning the
   metrics, or the single dimension table).
3. **Fan-out handling**: if metrics come from more than one base table (e.g.
   `sales` metrics + `budget` metrics), build one aggregated subquery (CTE) per
   base table grouped by the shared dimension keys, then join the CTEs on those
   keys. Single-base queries compile to a plain `SELECT ... GROUP BY`.
4. Resolve joins to dimension-only tables (e.g. `storeinfo`) via declared
   relationships.
5. Emit `SELECT <dims>, <metric exprs> FROM ... [JOIN ...] WHERE <filters>
   GROUP BY <dims> [ORDER BY ...] [LIMIT ...]`, all values parameterized.
Keep it readable; only generalize join-path resolution beyond direct
relationships if a second case needs it.

### 2.5 `engine/validator.py`
`validate(sql, ir, model)`: parse-light checks — SQL starts with `SELECT`/`WITH`
and contains no `;`-separated second statement nor DDL/DML keywords; every IR
field exists in the model. Optional `explain(sql, executor)` dry-run.

### 2.6 `engine/executor.py`
`SqliteExecutor(db_path)` with `run(sql, params) -> (columns, rows)` using a
read-only connection (`file:...?mode=ro` URI). Thin; dialect-agnostic interface.

### 2.7 `engine/planner.py`
`Planner` protocol: `plan(question, model, error=None) -> SemanticQuery`.
- `MockPlanner(rules)`: maps known questions (substring match) to canned IRs;
  used by tests and offline runs.
- `AnthropicPlanner(client, model_name)`: builds a system prompt from the
  semantic model (dimensions/metrics + synonyms + sample values + examples),
  calls Claude with a tool whose input schema is the IR JSON schema, parses the
  tool call into a `SemanticQuery`. On `error` provided, appends the prior SQL
  and error for repair.

### 2.8 `engine/engine.py`
`Engine(model, planner, dialect, executor, max_retries=1)` with
`ask(question) -> Result(rows, columns, sql, ir)`. Loop: plan → compile →
validate → execute; on failure, re-plan with the error up to `max_retries`.

### 2.9 `db/seed.py` & `models/sales.yml`
`seed.py` builds a fresh SQLite file with `sales`, `storeinfo`, `budget` and
inserts the fixed sample rows from spec §7 (incl. deleted/return rows and
matching daily budget). `models/sales.yml` is the model from spec §5.1 with
final column names matching the seed.

## 3. Build order (dependency-first)
1. Model loader + IR dataclasses (no deps on engine).
2. SQLite dialect + compiler (pure) — testable immediately.
3. `models/sales.yml` + `db/seed.py`.
4. Validator + Executor.
5. Engine orchestration + MockPlanner; end-to-end tests.
6. AnthropicPlanner + gated smoke test.
7. Postgres dialect stub + seam test.

Each step lands with its tests before the next (a module isn't done without a
test, per global guidelines). Commit per step.

## 4. Risks / mitigations
- **Fan-out correctness** is the subtle part — locked down by a dedicated
  compiler test asserting two-aggregate join for budget-vs-actual.
- **Planner drift** (LLM returns invalid field) — caught by the validator and
  fed into the repair loop; IR JSON schema constrains output up front.
- **Dialect leakage into compiler core** — all dialect-specific text goes
  through the `Dialect` interface; a Postgres seam test guards this.

## 5. Definition of done
All acceptance criteria in spec §6 pass; `uv run python -m unittest` is green
offline (Anthropic smoke test skipped without a key); `models/sales.yml`
answers the demo question end-to-end via the MockPlanner.
