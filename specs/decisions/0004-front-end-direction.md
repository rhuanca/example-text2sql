# ADR 0004 — Front-end direction: Streamlit now → Next.js + FastAPI later

Status: Accepted   ·   Date: 2026-07-16

## Context
The app is a Streamlit chat UI. As a client-facing product it will eventually need
multi-tenancy, auth/RBAC, and branding — areas where Streamlit is weak (full-rerun model,
concurrency limits, no native multi-tenancy). We assessed whether to migrate the front end now
(Chainlit, Reflex, or React/Next.js + FastAPI).

## Decision
**Stay on Streamlit for the MVP; do not migrate yet.** The backend is already fully decoupled
— `app.py` is the only Streamlit-coupled module; the engine, trace store, and chart logic
return framework-neutral objects — so a migration is a UI-only swap that is cheap to defer.
When multi-tenancy/auth/branding become real requirements, migrate to **Next.js/React +
FastAPI** (the Vercel AI SDK streams from a FastAPI Python endpoint, so the engine stays intact).

## Alternatives considered
- **Chainlit** — fastest swap and chat-native, but chat-first (fights the Model Map page + rich
  charts) and community-maintained since May 2025 — a durability risk for a sold product.
- **Reflex** — Python-native, compiles to React, but WebSocket-per-user/Redis scaling caveats.

## Consequences
- No migration cost now; the decoupled backend keeps it cheap later.
- A future enabler (not done): extract the framework-neutral glue from `app.py` into a
  `chat/session.py` so the swap is a drop-in.
