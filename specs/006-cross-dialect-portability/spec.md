# 006 — Cross-dialect portability
Status: Accepted   ·   Date: 2026-07-16   ·   Owner: rhuanca@gmail.com

## Problem / why
The product is sold to clients running their own **Postgres or MySQL** (and SQLite in the demo).
The same semantic model + question must compile to each engine's SQL — data never leaves the
client's database. Cross-dialect portability is a hard product requirement.

## Scope — what it does
One `SemanticQuery` IR compiles to SQLite, Postgres, or MySQL through a small `Dialect` seam.
Time grains (month/week/quarter/year truncation, and extract parts like month-of-year) are
**structured on the dimension** and lowered per dialect, instead of SQLite-only SQL snippets.

## Key decisions
- A `Dialect` protocol carries the per-engine differences: `quote_ident`, `placeholder`,
  `relative_date`, `limit_clause`, plus `date_trunc(unit, col)` / `date_part(part, col)`.
- Dimensions declare `grain`/`part` over a source `column`; the compiler renders them via the
  dialect (e.g. `date_trunc` / `DATE_FORMAT` / `date(...,'start of month')`), so one model is
  portable. Filter values are always bound parameters.
- SQLite is the live target; Postgres/MySQL compile and are unit-tested but not yet executed
  against a live DB (future work).

## Design
- `engine/dialects/base.py` — the `Dialect` protocol.
- `engine/dialects/{sqlite,postgres,mysql}.py` — one impl per DB.
- `engine/compiler.py` — `_col_sql` renders a dimension's grain/part through the dialect; the
  same IR flows unchanged.

## Acceptance / verification
- `tests/test_dialect_postgres.py`, `tests/test_dialect_mysql.py` — the same IR compiling to
  each dialect's SQL (interval/date-trunc/quoting differ, structure matches).
- `tests/test_compiler.py`, `tests/test_semantic_sql.py` — the SQLite live path.

## Out of scope / follow-ups
- Live Postgres/MySQL executors + dockerized integration tests (future).
- A `Dialect.cast` seam so a metric's `CAST(x AS REAL)` is portable (qbo runs on MySQL) — deferred.
