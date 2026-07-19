# 005 — Semantic-SQL front-end
Status: Accepted   ·   Date: 2026-07-15   ·   Owner: rhuanca@gmail.com

## Problem / why
Spec 001 had the LLM fill a fixed IR (never SQL). That schema can't grow to period comparisons,
window functions, or CTEs without becoming a query language of its own. See **ADR-0001**.

## Scope — what it does
The LLM authors **one SQL SELECT over a single _virtual_ table** whose columns are the model's
dimensions and metrics. The engine parses that SQL, **validates it against the model** (the
safety boundary), normalizes it into the `SemanticQuery` IR, and compiles deterministically.
Supports: SELECT of metric/dimension columns, WHERE (+ a `last_period(n, unit)` relative
window), GROUP BY, HAVING (on metrics), ORDER BY, LIMIT.

## Key decisions
- SQL is the *authoring language*; `SemanticQuery` stays the canonical IR (ADR-0001).
- Validation is the boundary: only known columns/functions, no joins, no physical tables,
  SELECT-only; literals become bound parameters in the compiler.
- Two non-flat shapes are recovered from the SQL rather than the IR: a **CASE-pivot** → a
  `Comparison` (spec detail in `compare.py`); **window functions** → outer-wrapped SQL + a
  `QueryShape` (compile a base aggregate, wrap the LLM's SELECT over `(base) AS base`).

## Design
- `engine/planner.py` — `AnthropicPlanner` emits SQL via a forced `emit_sql` tool; the system
  prompt (`build_system_prompt`) describes the virtual table.
- `engine/semantic_sql.py` — `compile_semantic_sql` is the single entry: `_parse` → `_basic_checks`
  (SELECT-only, no joins/subqueries, FROM the one virtual table) → route (`_has_window` →
  `_lower_window`; CASE-pivot → `Comparison`; else `_normalize` → `SemanticQuery`) → compile.
- `engine/validator.py` — SELECT-only + fields-must-exist guardrails.
- `engine/engine.py` — orchestrates plan → parse/validate → compile → validate_sql → execute with
  a **bounded repair loop** (`max_retries=1`): a recoverable error is fed back into the next plan.
- `engine/ir.py` — the normalized `SemanticQuery`; `QueryShape` for window/derived output.

## Acceptance / verification
- `tests/test_semantic_sql.py` — normalization, validation rejections (unknown/physical column,
  join, subquery, non-SELECT), pivots, windows, last_period windows, end-to-end execution.
- `tests/test_engine_e2e.py` — the repair loop; `tests/test_planner_anthropic.py` — live (auto-skips
  without a key).

## Out of scope / follow-ups
- Single CTE — spec 010. N-CTE compositional IR (`NamedQuery` + `SemanticQuery.ctes`) — future.
- Supersedes the "LLM never writes SQL" framing in specs 001 and 004.
