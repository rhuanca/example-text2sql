# 008 — Charts + storytelling
Status: Accepted   ·   Date: 2026-07-17   ·   Owner: rhuanca@gmail.com

## Problem / why
A result should answer the question, not just show a table. Chart choice must be deterministic
(no LLM in the visual, so it can't mislead), the chart should state the takeaway, and the same
chart specs must be portable to a future React front end (Vega-Lite — see **ADR-0002**).

## Scope — what it does
Pipeline **decide → narrate → build → place**:
- **Decide** (`charts.py`): pick a chart from the query shape — time→line, categorical→bar
  (top-N + muted "Other"), 2 categorical dims→heatmap, 1-time+2-categorical→**faceted small
  multiples**, 2 metrics→scatter option, scalar→number, else table.
- **Narrate** (`story.py`): a takeaway title, reference lines (avg/target/zero), peak/latest
  annotations, one-emphasis-color — computed deterministically from the data.
- **Build/place** (`plots.py`, `app.py`): themed Altair builders (line/area/bar/scatter/heatmap/
  faceted) behind one registered theme; a **per-result chart-type switcher**; signed metrics
  render as a **diverging** bar; the LLM prose summary sits above, additive.

## Key decisions
- Deterministic chart-from-shape; the narrate layer is pure (testable), no LLM.
- A period **Comparison** renders as grouped/clustered bars or a multi-series line (never
  stacked); the story headline uses growth-delta only for *temporal* period fields, and
  larger-vs-smaller framing for categorical ones (e.g. "Revenue exceeds Expense by 25%").
- **Honesty:** if the interpreted question asks for a breakdown the result doesn't contain, the
  UI says so (`missing_dimensions`) instead of silently over-promising.
- An exclusive-split comparison shows a tidy long-form table (not a half-zero pivot).

## Design
- `chat/charts.py` (`choose_chart`, `compatible_charts`, `ChartSpec`), `chat/story.py`
  (`choose_story`, `StorySpec`), `chat/plots.py` (theme + builders), `chat/app.py`
  (`render_chart` dispatch + the switcher + honesty note). The **Model Map** and **Evals** views
  live in the sidebar (`_render_model_map`, `_render_evals`).

## Acceptance / verification
- `tests/test_charts.py`, `tests/test_story.py`, `tests/test_app_helpers.py`,
  `tests/test_timeseries_viz.py` — selection, menus, story headlines, each builder's `.to_dict()`,
  the switcher, the missing-dimension note, the long-form table.

## Out of scope / follow-ups
- LLM-seeded titles for non-monotonic patterns; a React-portability spec-emitting seam.
