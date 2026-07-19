# ADR 0002 — Keep Vega-Lite as the charting library

Status: Accepted   ·   Date: 2026-07-18

## Context
Charts are generated programmatically from query results and must render in Streamlit today and
a React/Next.js front end later. We evaluated whether to switch off Altair/Vega-Lite (a
transitive dependency via Streamlit) to Plotly or ECharts, and separately researched what
agentic-BI products (Snowflake Cortex Analyst, Databricks Genie, ThoughtSpot, Tableau) generate.

## Decision
Keep **Vega-Lite** (authored via Altair). Its JSON specs are portable — the same spec renders in
Streamlit now and in React later — and it is the de-facto 2025-26 target for LLM/agent chart
generation (compact, schema-validated, secure-by-declaration). Invest in improvements the
library choice doesn't fix (a central theme, chart-type coverage, a switcher) rather than a
library swap.

## Alternatives considered
- **Plotly** — a true portability peer, best out-of-box interactivity, but a ~4.7MB bundle and a
  ~290-line + ~23-test rewrite for no clear win.
- **ECharts** — richest annotations, but JS-functions-in-option hurt pure-JSON portability and
  less LLM tooling.
- **Observable Plot / matplotlib** — fail the portability requirement (imperative / image-only).

## Consequences
- No rewrite, no new hard dependency; charts stay portable JSON for the eventual React front end.
- The agentic-BI common core (number/bar/line+area/table) is what our set already covers; we
  added area/scatter/heatmap/faceted small multiples and a per-result chart-type switcher as the
  second-tier gaps (spec 008).
