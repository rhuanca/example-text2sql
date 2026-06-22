# Spec 002 — Chat UI with plots

Status: Draft
Owner: rhuanca@gmail.com
Date: 2026-06-20
Depends on: Spec 001 (text-to-SQL engine)

## 1. Problem statement

Give the text-to-SQL engine a chat interface. A user types a question, the
engine answers, and the app shows a short written answer, a chart, the data
table, and (on demand) the generated SQL/IR. Keep it simple but useful.

## 2. Goals / Non-goals

### Goals
- A Streamlit chat app: type a question, see the answer in a conversation.
- For each answer show, in order: an **LLM prose summary**, an **auto-selected
  chart**, the **data table**, and an expander with the **SQL + IR**.
- Pick the chart **deterministically from the IR shape** (no extra LLM call for
  charting).
- Reuse the Spec 001 engine unchanged (model + AnthropicPlanner + SQLite).
- Keep the testable logic (chart selection, summary prompt/parse) pure and unit
  tested; the Streamlit layer stays thin.
- Graceful errors: a failed question shows a friendly message, never a crash.

### Non-goals (deferred)
- Auth, multi-user sessions, persistence across restarts (session-only history).
- Editing the semantic model from the UI.
- Postgres execution (still Spec-001-deferred).
- Rich chart configuration UI (chart is automatic; manual override is a later
  enhancement).

## 3. Architecture

```
   Streamlit app (chat loop)
        │  question
        ▼
   Engine.ask(question)            ← Spec 001, unchanged
        │  Result(ir, sql, columns, rows)
        ├──────────────► Summarizer (LLM)  → prose answer
        ├──────────────► choose_chart(ir, columns, rows) → ChartSpec (pure)
        └──────────────► render: summary + chart + table + SQL/IR expander
```

New code lives under `text2sql/chat/`:
- `charts.py` — `choose_chart(ir, columns, rows) -> ChartSpec`. Pure, deterministic.
- `summarizer.py` — `Summarizer` protocol + `AnthropicSummarizer` + `MockSummarizer`.
- `app.py` — the Streamlit script (thin; orchestrates engine + summarizer +
  charts and renders).

## 4. Chart selection (deterministic, from the IR)

`ChartSpec(kind, x, y, series)` where `kind ∈ {number, line, bar, table}`.

Algorithm:
1. Effective dimensions = the IR's dimensions whose result column has **>1
   distinct value** (drop constants pinned by filters, e.g. a single product).
2. `y` = the metric columns.
3. Decide:
   - no metrics → `table`.
   - 0 effective dims, ≥1 metric → `number` (show the scalar metric value(s)).
   - 1 effective dim:
     - time-like dim (name in {date, iso_week, iso_year, week, day, month,
       quarter} or `type` is date/number used as a sequence) → `line`, x = dim.
     - else → `bar`, x = dim.
   - 2 effective dims, exactly one time-like → `line`, x = time dim,
     series = the other dim.
   - otherwise → `table`.
4. Always fall back to `table` if the chosen columns aren't present.

This makes the flagship "Dozen Glazed week over week" render as a line over
`iso_week` (product/year are constants and drop out), and "net sales by market"
render as a bar.

## 5. Prose summary

`AnthropicSummarizer.summarize(question, columns, rows) -> str`:
- System prompt: "summarize these query results for a business user in 1–2
  sentences; cite the key numbers; do not invent data."
- Sends the question plus a compact rendering of the result (cap at ~50 rows).
- Returns plain text. Reuses `text2sql/config.py` for key/model.
- `MockSummarizer` returns a deterministic string for tests.
- If summarization fails (e.g. no key), the app still shows chart + table and a
  neutral fallback line — summary is additive, never blocking.

## 6. App behavior / acceptance criteria

1. `uv run streamlit run text2sql/chat/app.py` launches a chat UI.
2. On first run, if `demo.db` is missing it is seeded automatically.
3. Asking "How is Dozen Glazed performing week over week?" shows: a prose
   summary, a **line chart** over ISO week, a data table, and an expander
   revealing the SQL and IR.
4. Asking "net sales by market" shows a **bar chart**.
5. Asking a scalar question (a metric, no dimension) shows a **number**.
6. A question the engine can't answer shows a friendly error bubble; the app
   keeps running and accepts the next question.
7. Conversation history persists within the session (scrollback of prior Q&A).
8. Sidebar shows the model name and a few example questions.

## 7. Testing strategy

Streamlit scripts aren't unit-tested directly; we test the pure pieces and keep
`app.py` thin enough to eyeball.
- `test_charts.py`: `choose_chart` for scalar→number, time-dim→line,
  categorical→bar, constant-dim-dropped (WoW example→line), multi-dim→table,
  no-metric→table.
- `test_summarizer.py`: `AnthropicSummarizer.summarize` with a mock client
  builds the right prompt and returns its text; `MockSummarizer` is
  deterministic. A live summarizer test is gated on an API key (skipped without).
- `app.py`: a thin import-safe smoke (logic factored into importable helpers).

## 8. Dependencies
- `streamlit` (pulls `pandas`); add `pandas` explicitly for the chart frames.
- No new charting dep — use Streamlit's native `st.line_chart` / `st.bar_chart`
  / `st.metric` / `st.dataframe`.

## 9. Open items
- Whether to cache the `Engine`/clients across reruns with
  `st.cache_resource` (proposed: yes, to avoid re-instantiating per keystroke).
- Multi-series line styling for the 2-dim case (proposed: pivot to wide frame,
  native chart, revisit if it looks poor).
