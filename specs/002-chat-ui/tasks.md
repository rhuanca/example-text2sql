# Tasks 002 — Chat UI with plots

Spec: ./spec.md  Plan: ./plan.md

## U1 — Chart selection (pure)  (`text2sql/chat/charts.py`, `tests/test_charts.py`)
- `ChartSpec` dataclass + `choose_chart(ir, columns, rows)` + `is_time_like`.
- Tests: scalar metric→number; time dim→line; categorical dim→bar; WoW example
  (constant product/year dropped)→line over iso_week; 2+ effective dims→table;
  no metric→table.
- DoD: chart tests pass; no non-stdlib imports in charts.py.

## U2 — Deps
- `uv add streamlit pandas`; confirm import.
- DoD: `uv run python -c "import streamlit, pandas"` works.

## U3 — Summarizer  (`text2sql/chat/summarizer.py`, `tests/test_summarizer.py`)
- `Summarizer` protocol, `MockSummarizer`, `AnthropicSummarizer`,
  `build_summary_prompt`.
- Tests: prompt includes the question + a row; MockSummarizer deterministic;
  AnthropicSummarizer with a fake client returns its text; live test gated on key.
- DoD: summarizer tests pass.

## U4 — Streamlit app  (`text2sql/chat/app.py`)
- Cached engine/summarizer; `to_frame`; `render_result`; chat loop; sidebar.
- Importable helpers (`to_frame`, `render_result` factored) for a smoke test.
- DoD: `uv run streamlit run text2sql/chat/app.py` launches and answers the
  three demo questions with the expected chart kinds (manual check).

## U5 — Docs + sweep
- README: "Chat UI" section with the run command and example questions.
- Full suite green offline.
- DoD: clean `uv run python -m unittest`.
