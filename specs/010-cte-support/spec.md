# 010 — Single-CTE support
Status: Accepted   ·   Date: 2026-07-19   ·   Owner: rhuanca@gmail.com

## Problem / why
The flat `SemanticQuery` IR can't express multi-step analytics — aggregate-then-aggregate,
top-N / month-over-month per group, ratios or filters over an aggregate. The LLM already
authors SQL (spec 005 / ADR-0001), so it can write `WITH … SELECT …`; the engine must validate
and lower it.

## Scope — what it does
Accept **exactly one** top-level CTE: `WITH b AS (<semantic aggregate>) SELECT <cols/exprs>
FROM b [GROUP BY][ORDER][LIMIT]`. Unlocks aggregate-then-aggregate, top-N / month-over-month per
group (a window in the outer over CTE columns), and ratios/filters over an aggregate.

## Key decisions
- Reuse the window path (`_lower_window`): a CTE is its named, explicit generalization — compile
  the CTE body with the ordinary compiler, wrap the outer over it, return a `QueryShape`.
- **Scoped validation** is the boundary: the CTE body is validated as a plain semantic aggregate
  (no window, no pivot); the outer may only `FROM` the CTE and reference the CTE's **output
  columns** — no model fields, no physical tables, no joins/subqueries, SELECT-only.
- No IR change: emits a real `WITH`, carries a `QueryShape` (execution-tested; not IR-component-F1,
  same as windows).

## Design
- `engine/semantic_sql.py` — `compile_semantic_sql` routes a top-level `WITH` to `_lower_cte`;
  `_lower_cte` compiles the body (`_normalize` → `compile_ir`), `_validate_cte_outer` checks the
  outer against the CTE's output scope, then emits `WITH name AS (<compiled body>) <outer>`.

## Acceptance / verification
- `tests/test_semantic_sql.py::TestCte` — month-over-month happy path (compiles to a real WITH,
  runs, plan is a `QueryShape`) + rejections (outer column not in the CTE, physical table in the
  outer, a second CTE, a windowed CTE body).

## Out of scope / follow-ups
- N chained CTEs + a first-class **compositional IR** (`NamedQuery` + `SemanticQuery.ctes`) so CTE
  queries are normalized/testable by component-F1; outer subqueries; CTE↔model joins; unifying
  `Comparison`/`QueryShape` into the compositional IR.
