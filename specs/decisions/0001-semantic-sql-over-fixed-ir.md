# ADR 0001 — Semantic SQL over a fixed IR

Status: Accepted   ·   Date: 2026-07-15

## Context
Spec 001 shipped the engine as **NL → the LLM fills a fixed Semantic Query IR (JSON) →
deterministic compiler → SQL**. The LLM never wrote SQL; it emitted a structured object of
metrics/dimensions/filters. That kept the model tightly constrained, but the fixed JSON schema
is a poor authoring surface: expressing anything beyond a flat aggregate (period comparisons,
window functions, later CTEs) means inventing a mini query-language in JSON, and LLMs are less
reliable filling a bespoke schema than writing SQL they've seen millions of.

## Decision
Let the LLM **author SQL over a single _virtual_ table** whose columns are the model's
dimensions and metrics. Parse it (sqlglot) and **validate it against the model** — the safety
boundary — then normalize it into the existing `SemanticQuery` IR and compile deterministically.
So the LLM writes SQL, but the SQL is the *authoring language*; the `SemanticQuery` remains the
canonical IR the compiler consumes, and validation guarantees only model fields, no joins, no
physical tables, SELECT-only. (See spec 005.)

## Alternatives considered
- **Keep the fixed IR (status quo).** Simpler engine, no SQL parser — but a schema that can't
  grow to comparisons/windows/CTEs without becoming a query language of its own.
- **LLM emits SQL, run it directly (no semantic layer).** Maximum expressiveness, zero safety —
  hallucinated columns/joins, injection, non-determinism. Rejected.

## Consequences
- Gained expressiveness: CASE-pivots (→ `Comparison`), window functions (→ `QueryShape`
  outer-wrapping), and single CTEs (spec 010) are all natural to author.
- Cost: a semantic-SQL front-end (`engine/semantic_sql.py`) that must be kept in lockstep with
  the IR and stay strict (the validator is the boundary).
- The `SemanticQuery` is still the IR; some shapes (windows, CTEs) have no flat IR form and
  carry a `QueryShape` instead — a compositional IR is the eventual generalization.
- **Supersedes** the "LLM never writes SQL" framing in specs 001 and 004.
