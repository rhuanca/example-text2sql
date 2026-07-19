# ADR 0003 — Local-first observability (traces.db); LangSmith optional

Status: Accepted   ·   Date: 2026-07-16

## Context
Moving from PoC to a product sold to clients, we needed conversation persistence and token/cost
tracking for analysis. Options ranged from a managed platform (LangSmith) as the system of
record, to a custom local store, to vendor-neutral OpenTelemetry.

## Decision
A **hybrid**: a local `traces.db` (sqlite) is the **system of record** — conversations, turns,
and per-call token usage we own and can `SELECT` for analysis — with **LangSmith kept as an
optional dev toggle** (gated by `LANGSMITH_TRACING`, off by default, no data leaves infra unless
enabled). One token-capture seam (`trace/usage.py`) feeds both. (See spec 007.)

## Alternatives considered
- **LangSmith as the system of record.** Near-zero code, great UI — but client data leaves their
  infra to a US SaaS (a residency blocker for a sellable product) and analysis is locked in its
  UI, not SQL-queryable.
- **OpenTelemetry.** Vendor-neutral and standard, but the heaviest setup and still needs a
  backend — speculative generality for an MVP.

## Consequences
- Client data stays in-infra; the quality/usage record is a real table we query.
- Deferred: `user_id`/`tenant_id` columns (until auth), Postgres/MySQL port of the store (the
  dialect seam is ready). Aligns with the portable-semantic-layer product goal.
