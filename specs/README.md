# Specs

Spec-driven development for this project, **right-sized as _spec-lite_**: we plan every
feature before building it, and we **persist that plan as a committed spec** so the record
never drifts from the code (it did once — see the superseded notes in 001/004).

## Workflow

1. Plan the feature (what, why, key decisions, how, acceptance).
2. Build it.
3. **Commit the plan as `specs/NNN-<slug>/spec.md` with the feature.** The spec is essentially
   the approved plan — near-zero extra work.
4. If the change is **architectural** (a pivot, a library choice, a data-flow change), also add
   an **ADR** under `specs/decisions/`.
5. When a later change supersedes a spec, don't delete it — add a short **Superseded by …**
   note at the top and keep the rest as historical record.

The full `spec.md` + `plan.md` + `tasks.md` triad (as in 001–003) is reserved for genuinely
large features; everything else is one `spec.md`.

## Spec-lite template (`specs/NNN-<slug>/spec.md`)

```
# NNN — Title
Status: Accepted | Draft | Superseded   ·   Date: YYYY-MM-DD   ·   Owner: <email>

## Problem / why
## Scope — what it does
## Key decisions            (link ADRs)
## Design                   (modules + data flow, brief)
## Acceptance / verification (tests, commands)
## Out of scope / follow-ups
```

## ADR template (`specs/decisions/NNNN-<slug>.md`)

```
# ADR NNNN — Title
Status: Accepted   ·   Date: YYYY-MM-DD

## Context
## Decision
## Alternatives considered
## Consequences
```

## Inventory

### Feature specs
| # | Feature | Status |
|---|---|---|
| 001 | Text-to-SQL engine (IR + compiler) | Accepted — *IR-authoring parts superseded by 005* |
| 002 | Chat UI | Accepted |
| 003 | Eval harness | Accepted |
| 004 | QBO semantic-model POC | Accepted — *pre-pivot note superseded by 005* |
| 005 | Semantic-SQL front-end | Accepted |
| 006 | Cross-dialect portability | Accepted |
| 007 | Observability + token tracking | Accepted |
| 008 | Charts + storytelling | Accepted |
| 009 | Eval quality-tracking | Accepted |
| 010 | Single-CTE support | Accepted |

### Decisions (ADRs)
| # | Decision |
|---|---|
| 0001 | Semantic SQL over a fixed IR (the pivot) |
| 0002 | Keep Vega-Lite as the charting library |
| 0003 | Local-first observability (traces.db); LangSmith optional |
| 0004 | Front-end direction: Streamlit now → Next.js + FastAPI later |
| 0005 | Evals are the regression contract (don't edit to pass) |
