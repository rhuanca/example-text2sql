# 009 — Eval quality-tracking
Status: Accepted   ·   Date: 2026-07-19   ·   Owner: rhuanca@gmail.com

## Problem / why
The eval harness (spec 003) scored each run and threw the result away. As a product we need to
**track agent quality over time** — a score, a persisted history, and a visual trend showing
whether quality is improving or regressing.

## Scope — what it does
- A per-run **scorecard** (per model): execution accuracy, pass counts, IR-component F1,
  timestamp, git sha — built from the existing `Report`.
- A committed **history** (`eval/history.jsonl`) appended by `--record` on the eval CLI, so the
  trend travels with the repo.
- An **Evals view** in the app: a per-model execution-accuracy **trend line** over time,
  first→last delta captions (improving/regressing), and the latest scorecard table.

## Key decisions
- Simple first increment: track execution accuracy per model on the existing per-model cases;
  no LLM-judge yet.
- History is committed JSONL (diffable, travels with the repo), not the gitignored `traces.db`.
- **Evals are the regression contract** — see **ADR-0005**; new coverage goes in `tests/`, not by
  editing eval cases.

## Design
- `eval/history.py` — `Scorecard.from_report` + `append_scorecard`/`load_history` (stdlib only).
- `eval/run.py` — `--record` appends the scorecard.
- `chat/app.py` — `_render_evals` (trend via `plots.line_chart`) + `eval_summary` (per-model
  latest + delta).

## Acceptance / verification
- `tests/test_eval_history.py` — scorecard build/serialize, history round-trip, `eval_summary`.
- Live: `python -m text2sql.eval.run --record` (+ qbo variant) grows `eval/history.jsonl`; the
  Evals view shows the trend.

## Out of scope / follow-ups
- Auto-cases from each model's `verified_queries`; offline product-behavior assertions that run
  every commit; a validated LLM-as-judge for prose/chart quality.
