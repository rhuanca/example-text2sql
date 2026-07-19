# 007 — Observability + token tracking
Status: Accepted   ·   Date: 2026-07-16   ·   Owner: rhuanca@gmail.com

## Problem / why
As an MVP sold to clients we need conversation persistence and per-call token/cost tracking for
analysis and product improvement — kept in our own infra. See **ADR-0003**.

## Scope — what it does
- A stable **thread id** per browser session (persists across dataset switches).
- Every turn logged to a local **`traces.db`** (sqlite): `conversations`, `turns`, `llm_calls`.
- **Token usage** captured per LLM call (rewrite / plan / summary) with input/output tokens + ms,
  surfaced in the UI and stored.
- **Optional LangSmith** tracing, gated by `LANGSMITH_TRACING` (off by default).

## Key decisions
- Local `traces.db` is the system of record; LangSmith is an optional dev toggle (ADR-0003).
- One token-capture seam via a contextvar collector, so both the local store and LangSmith read
  the same numbers; capture is best-effort — a store failure never breaks the answer.
- `user_id`/`tenant_id` deferred until auth lands (thread id only for now).

## Design
- `trace/usage.py` — `LlmCall` + a contextvar `collect()`/`record_usage()`; recorded at the three
  `messages.create` sites (planner, rewriter, summarizer).
- `trace/store.py` — `TraceStore` over stdlib sqlite3; idempotent schema; `record_turn` writes a
  turn + its calls in one transaction; all writes best-effort.
- `trace/llm.py` — shared `build_anthropic_client` (LangSmith wrap) + `tool_input`.
- `chat/app.py` — mints the thread id, wraps each turn in `usage.collect()`, records the turn,
  shows a token caption. `engine.ask(thread_id=…)` tags the LangSmith run.
- Config: `config.get_trace_db_path()` (env `TRACE_DB_PATH`); `traces.db` is gitignored.

## Acceptance / verification
- `tests/test_usage.py`, `tests/test_trace_store.py` — the collector + the store (schema, dedup,
  best-effort). `tests/test_app_helpers.py` — the turn→row mapping.
- Live: a two-turn session writes one conversation + two turns + non-zero `llm_calls`.

## Out of scope / follow-ups
- `user_id`/`tenant_id`; Postgres/MySQL port of the store (the dialect seam is ready).
