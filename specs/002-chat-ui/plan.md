# Plan 002 — Chat UI with plots (technical plan)

Status: Draft
Spec: ./spec.md
Date: 2026-06-20

Defaults accepted: cache Engine/clients with `st.cache_resource`; 2-dim line
case pivots to a wide frame and uses the native chart.

## 1. Stack & deps
- `streamlit` + `pandas` (`uv add streamlit pandas`).
- Reuse Spec 001: `Engine`, `AnthropicPlanner`, `SqliteExecutor`, `load_model`,
  `build_database`, `config`.
- Tests: stdlib `unittest`.

## 2. Modules

### 2.1 `text2sql/chat/charts.py`  (pure)
```
@dataclass ChartSpec: kind: str; x: str|None; y: list[str]; series: str|None
TIME_LIKE = {"date","day","month","quarter","week","iso_week","iso_year","year"}

def choose_chart(ir, columns, rows) -> ChartSpec
```
- effective dims = [d for d in ir.dimensions if distinct_count(col d in rows) > 1]
- y = [m for m in ir.metrics if m in columns]
- branch per spec §4; fall back to table if chosen columns absent.
- helper `is_time_like(dim_name)`; distinct count computed from rows by column
  index (map column name -> index).
- no pandas dependency here (keep it pure/trivially testable).

### 2.2 `text2sql/chat/summarizer.py`
```
class Summarizer(Protocol): summarize(question, columns, rows) -> str
class MockSummarizer:  # returns f"{len(rows)} rows."
class AnthropicSummarizer:
    __init__(client=None, model=None, max_rows=50)
    summarize(...) -> str
```
- builds a compact text table (cap max_rows) + question; one Anthropic call;
  returns concatenated text blocks. Reuses `config` for key/model.
- `build_summary_prompt(question, columns, rows)` factored out and unit-tested.

### 2.3 `text2sql/chat/app.py`  (thin Streamlit script)
- `@st.cache_resource get_engine()` → builds demo.db if missing, loads model,
  wires Engine(AnthropicPlanner). `@st.cache_resource get_summarizer()`.
- `render_result(result, summarizer)`: prose summary (try/except → fallback),
  chart via `choose_chart` + `to_frame`, `st.dataframe`, expander with SQL+IR.
- `to_frame(columns, rows)`: small pandas helper (in app.py, not charts.py).
- chat loop: `st.session_state.history` list of (role, payload); replay on each
  run; `st.chat_input` drives a new turn.
- sidebar: model name + example questions.
- main body guarded so importing the module doesn't require a running server
  for the helper functions (helpers importable; `st.*` calls run at module top
  as Streamlit expects).

## 3. Build order
1. `charts.py` + `test_charts.py` (pure, no deps beyond stdlib).
2. `uv add streamlit pandas`.
3. `summarizer.py` + `test_summarizer.py` (mock client; gated live test).
4. `app.py` (manual launch check) + import-safe helper smoke test.
5. README section: how to run the chat app.

## 4. Risks / mitigations
- **Streamlit testability**: keep all logic in pure functions
  (`choose_chart`, `build_summary_prompt`, `to_frame`); test those, not the
  rendered page.
- **Summary blocking the answer**: wrap summarize in try/except; chart+table
  always render.
- **Chart picks a bad shape**: deterministic rules + table fallback; the raw
  table is always shown so no information is lost.

## 5. Definition of done
Spec §6 acceptance criteria hold; `choose_chart` and summary-prompt tests pass;
full suite green offline; `uv run streamlit run text2sql/chat/app.py` answers the
three demo questions with the expected chart kinds.
