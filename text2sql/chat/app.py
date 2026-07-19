"""Streamlit chat UI for the text-to-SQL engine.

Run it:
    uv run streamlit run text2sql/chat/app.py

This module is the app *shell*: dataset selection, engine wiring, planner memory,
and Streamlit placement. Chart selection lives in charts.py; chart construction
(the palette, formatting, and Altair builders) lives in plots.py. Here we only
decide-with-choose_chart, build-with-plots, and place with st.*.
"""

from __future__ import annotations

import os
import sys
import time
import uuid

# `streamlit run text2sql/chat/app.py` executes this file as a top-level script,
# so the package root is not on sys.path and relative imports would fail. Put the
# repo root on the path and use absolute imports so it works both as a script and
# when imported as text2sql.chat.app.
REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from dataclasses import dataclass, field

from text2sql.config import get_api_key, get_model, get_trace_db_path
from text2sql.db.seed import build_database as build_sales_db
from text2sql.db.seed_qbo import build_database as build_qbo_db
from text2sql.engine.dialects.sqlite import SqliteDialect
from text2sql.engine.engine import Engine, EngineError
from text2sql.engine.executor import SqliteExecutor
from text2sql.engine.planner import AnthropicPlanner, PlannerError
from text2sql.engine.rewriter import AnthropicRewriter
from text2sql.semantic.model import load_model
from text2sql.chat.charts import choose_chart, compatible_charts
from text2sql.chat.story import choose_story
from text2sql.chat.model_map import model_to_dot, table_fields
from text2sql.chat.plots import (
    _display_frame, _fmt_number, _md_safe, _percent_measure, area_chart,
    bucket_long_tail, chart_frame, comparison_grouped_bar, comparison_long, d3_format,
    grouped_bar, heatmap, horizontal_bar, line_chart, line_panel, scatter_chart,
    stacked_bar, to_frame, vertical_grouped_bar,
)
from text2sql.chat.summarizer import AnthropicSummarizer, MockSummarizer
from text2sql.trace import usage
from text2sql.trace.store import TraceStore


# ---- dataset registry -----------------------------------------------------
@dataclass(frozen=True)
class Dataset:
    """A selectable semantic model + its synthetic SQLite database."""

    key: str
    label: str
    model_path: str
    db_path: str
    build_db: object  # callable(path) -> str, seeds the database
    placeholder: str
    examples: list = field(default_factory=list)


# Anchor data paths to the repo root so they resolve regardless of cwd.
DATASETS = {
    "sales": Dataset(
        key="sales",
        label="Product sales demo",
        model_path=os.path.join(REPO_ROOT, "models", "sales.yml"),
        db_path=os.path.join(REPO_ROOT, "demo.db"),
        build_db=build_sales_db,
        placeholder="Ask about sales, budget, or stores…",
        examples=[
            "How is Cappuccino performing week over week?",
            "What were total net sales by market?",
            "Budget vs actual by store",
        ],
    ),
    "qbo": Dataset(
        key="qbo",
        label="QuickBooks finance (QBO)",
        model_path=os.path.join(REPO_ROOT, "models", "qbo.yml"),
        db_path=os.path.join(REPO_ROOT, "demo_qbo.db"),
        build_db=build_qbo_db,
        placeholder="Ask about revenue, expenses, departments, entities…",
        examples=[
            "Show revenue by month",
            "What are our top 10 expense accounts?",
            "Compare invoiced amount to posted amount by entity",
        ],
    ),
}
DEFAULT_DATASET = "sales"


# ---- planner memory helper ------------------------------------------------
def recent_turns(history: list, limit: int = 4) -> list:
    """Extract the last `limit` answered turns from the chat history as
    {"question", "ir"} pairs, for the planner's short-term memory. Only turns
    that produced a result count (error turns carry no IR to build on); the
    question is the user message immediately preceding each assistant answer."""
    turns = []
    pending_q = None
    for msg in history:
        if msg["role"] == "user":
            pending_q = msg["text"]
        elif msg["role"] == "assistant" and msg.get("result") is not None and pending_q:
            turns.append({"question": pending_q, "ir": msg["result"].ir.to_dict()})
            pending_q = None
    return turns[-limit:]


# ---- engine wiring --------------------------------------------------------
def build_engine(dataset_key: str = DEFAULT_DATASET) -> Engine:
    ds = DATASETS[dataset_key]
    if not os.path.exists(ds.db_path):
        ds.build_db(ds.db_path)
    model = load_model(ds.model_path)
    if not get_api_key():
        raise PlannerError("ANTHROPIC_API_KEY is not set — the chat app needs it.")
    planner = AnthropicPlanner()
    return Engine(model, planner, SqliteDialect(), SqliteExecutor(ds.db_path),
                  rewriter=AnthropicRewriter())


def build_summarizer():
    return AnthropicSummarizer() if get_api_key() else MockSummarizer()


def build_trace_store() -> TraceStore:
    store = TraceStore(get_trace_db_path())
    store.init()  # idempotent; best-effort (never raises)
    return store


def record_turn(store, thread_id, dataset, question, payload, calls, latency_ms) -> None:
    """Persist one answered (or failed) turn + its LLM calls. Best-effort at the
    store layer, so this never breaks the answer path."""
    result = payload.get("result")
    store.record_turn(
        thread_id=thread_id,
        dataset=dataset,
        question=question,
        rewritten=getattr(result, "rewritten", None),
        semantic_sql=getattr(result, "semantic_sql", None),
        sql=getattr(result, "sql", None),
        row_count=len(result.rows) if result is not None else None,
        chart_kind=payload.get("chart_kind"),
        error=payload.get("error"),
        latency_ms=latency_ms,
        calls=calls,
    )


def reset_session(session_state) -> None:
    """Start a fresh conversation: clear the chat and mint a new thread_id (the old
    conversation's rows stay in traces.db). Dict-style access so it works with both
    st.session_state and a plain dict (tests)."""
    session_state["history"] = []
    session_state["thread_id"] = uuid.uuid4().hex


def safe_summarize(summarizer, question, columns, rows) -> str:
    try:
        text = summarizer.summarize(question, columns, rows)
        return text or _fallback_summary(rows)
    except Exception:
        return _fallback_summary(rows)


def _fallback_summary(rows) -> str:
    return f"Returned {len(rows)} row(s)." if rows else "No matching data found."


# ---- Streamlit rendering (only runs under `streamlit run`) -----------------
def render_chart(st, kind, spec, result, units, additive, types, story):
    """Draw ONE chart of the given `kind` from the result + spec. The switcher calls
    this with the user's selected kind (defaulting to choose_chart's pick), so the
    same result can be re-rendered as a different chart. `kind == "table"` is a no-op
    (the app always shows the table below)."""
    ir = result.ir
    if hasattr(ir, "period_field") and kind in ("line", "bar"):
        # A period comparison: melt the wide pivot and render a trend line (week over
        # week) or grouped bars (periods side by side) — never stacked.
        long = comparison_long(ir, result.columns, result.rows)
        fmt = d3_format(units.get(ir.metric))
        if kind == "line":
            st.altair_chart(line_chart(long, "period", "value", color=ir.split_by,
                                       fmt=fmt, story=story), use_container_width=True)
        elif spec.orientation == "clustered":
            st.altair_chart(vertical_grouped_bar(long, ir.split_by, ir.period_field,
                            ir.periods, fmt=fmt, x_type=types.get(ir.split_by),
                            story=story), use_container_width=True)
        else:
            st.altair_chart(comparison_grouped_bar(long, ir.split_by, ir.period_field,
                            ir.periods, fmt=fmt, story=story), use_container_width=True)
        return

    fmt = d3_format(units.get(spec.y[0])) if spec.y else ","
    if kind == "number" and result.rows:
        cols = st.columns(len(spec.y))
        for c, metric in zip(cols, spec.y):
            idx = result.columns.index(metric)
            c.metric(metric, _fmt_number(result.rows[0][idx], units.get(metric)))
    elif kind == "line":
        df = to_frame(result.columns, result.rows)
        metric_units = {units.get(m) for m in spec.y}
        same_scale = len(metric_units) == 1 and None not in metric_units
        if len(spec.y) > 1 and not spec.series and not same_scale:
            # measures of different (or unknown) scale get one panel each so neither
            # is squashed onto the other's axis.
            for metric in spec.y:
                st.caption(metric.replace("_", " "))
                st.altair_chart(line_panel(df, spec.x, metric,
                                _percent_measure(metric, units), x_type=types.get(spec.x)),
                                use_container_width=True)
        elif spec.series:
            st.altair_chart(line_chart(df, spec.x, spec.y[0], color=spec.series, fmt=fmt,
                            x_type=types.get(spec.x), story=story),
                            use_container_width=True)
        elif len(spec.y) == 1:
            st.altair_chart(line_chart(df, spec.x, spec.y[0], fmt=fmt,
                            x_type=types.get(spec.x), story=story),
                            use_container_width=True)
        else:  # 2+ same-scale measures -> one line per measure
            long_df = df.melt(id_vars=[spec.x], value_vars=spec.y,
                              var_name="measure", value_name="value")
            st.altair_chart(line_chart(long_df, spec.x, "value", color="measure", fmt=fmt,
                            x_type=types.get(spec.x), story=story),
                            use_container_width=True)
    elif kind == "area":
        df = to_frame(result.columns, result.rows)
        if len(spec.y) > 1 and not spec.series:
            long_df = df.melt(id_vars=[spec.x], value_vars=spec.y,
                              var_name="measure", value_name="value")
            st.altair_chart(area_chart(long_df, spec.x, "value", color="measure", fmt=fmt,
                            x_type=types.get(spec.x)), use_container_width=True)
        else:
            st.altair_chart(area_chart(df, spec.x, spec.y[0], color=spec.series, fmt=fmt,
                            x_type=types.get(spec.x), story=story),
                            use_container_width=True)
    elif kind == "scatter" and len(spec.y) >= 2:
        st.altair_chart(scatter_chart(to_frame(result.columns, result.rows),
                        spec.y[0], spec.y[1], label=spec.x,
                        fmt_x=fmt, fmt_y=d3_format(units.get(spec.y[1]))),
                        use_container_width=True)
    elif kind == "heatmap":
        st.altair_chart(heatmap(to_frame(result.columns, result.rows), spec.x,
                        spec.series, spec.y[0], fmt=fmt), use_container_width=True)
    elif kind == "bar" and spec.orientation == "grouped":
        st.altair_chart(grouped_bar(to_frame(result.columns, result.rows), spec.x,
                        spec.y, fmt=fmt), use_container_width=True)
    elif kind == "bar" and spec.orientation == "horizontal":
        # top-N + muted "Other" bucket, sorted last, shared order across measures.
        cols, rows = bucket_long_tail(result.columns, result.rows, spec.x, spec.y[0])
        df = to_frame(cols, rows)
        muted = "Other" if any(r[cols.index(spec.x)] == "Other" for r in rows) else None
        order = df.sort_values(spec.y[0], ascending=False)[spec.x].tolist()
        if muted:
            order = [c for c in order if c != "Other"] + ["Other"]
        for metric in spec.y:
            if len(spec.y) > 1:
                st.caption(metric.replace("_", " "))
            st.altair_chart(horizontal_bar(df, spec.x, metric, sort=order,
                            fmt=d3_format(units.get(metric)), story=story, mute=muted),
                            use_container_width=True)
    elif kind == "bar" and spec.orientation == "stacked":
        st.altair_chart(stacked_bar(to_frame(result.columns, result.rows), spec.x,
                        spec.series, spec.y[0], fmt=fmt, x_type=types.get(spec.x)),
                        use_container_width=True)
    elif kind == "bar":
        st.bar_chart(chart_frame(spec, result.columns, result.rows))
    # kind == "table": no-op — the caller always renders the table below.


def _render_assistant(st, payload, units=None, additive=None, types=None):
    units = units or {}
    additive = additive or {}
    types = types or {}
    if payload.get("error"):
        st.error(payload["error"])
        return
    result = payload["result"]
    if getattr(result, "rewritten", None):
        # scope carried from earlier turns — show it so it's transparent + reversible
        st.caption(f"Interpreted as: {result.rewritten}")
    st.markdown(_md_safe(payload["summary"]))

    spec = choose_chart(result.ir, result.columns, result.rows,
                        units=units, additive=additive, types=types)
    payload["chart_kind"] = spec.kind  # captured for the trace store
    story = choose_story(result.ir, spec, result.columns, result.rows, units, types)
    if not result.rows:
        st.info("No matching data for this question.")
    else:
        # Auto-pick the recommended chart, but let the user switch type (every agentic
        # BI tool does this). options[0] is choose_chart's pick; the selection persists
        # per message via a stable id key.
        payload.setdefault("id", uuid.uuid4().hex)
        options = compatible_charts(result.ir, result.columns, result.rows,
                                    units, additive, types)
        selected = options[0]
        if len(options) > 1:
            selected = st.segmented_control(
                "Chart type", options, default=options[0],
                key=f"chart_{payload['id']}", label_visibility="collapsed") or options[0]
        # Story overlays (title/annotations) only fit the recommended kind; drop them
        # when the user overrides to a different chart.
        render_chart(st, selected, spec, result, units, additive, types,
                     story if selected == spec.kind else None)

    st.dataframe(_display_frame(result, types), use_container_width=True)
    with st.expander("Show SQL"):
        if result.semantic_sql:
            st.caption("Semantic SQL — written by the assistant")
            st.code(result.semantic_sql, language="sql")
            st.caption("Compiled SQL — what actually ran")
        st.code(result.sql, language="sql")
        calls = payload.get("calls")
        if calls:
            tin, tout = usage.totals(calls)
            st.caption(f"Tokens — input {tin:,} · output {tout:,} · {len(calls)} LLM call(s)")


def _render_model_map(st, model):
    """The Model Map view: a color-coded star-schema diagram plus a per-table
    inspector. Reads only the semantic model — no database, no LLM."""
    st.markdown(
        "This is the whole vocabulary the assistant is allowed to use. It can "
        "**only** pick metrics, dimensions, and filters from these tables — it "
        "never writes free-form SQL, so it can't invent a column or a join."
    )
    st.graphviz_chart(model_to_dot(model), use_container_width=True)
    st.caption("🟨 fact tables (carry metrics)   🟦 dimension tables   ·   arrows show join keys")

    st.subheader("Inspect a table")
    table = st.selectbox(
        "table", model.tables, format_func=lambda t: f"{t.name}  ({t.table})"
    )
    fields = table_fields(model, table.name)

    if table.grain:
        st.markdown(f"**Grain:** {table.grain}")
    if table.description:
        st.markdown(table.description)

    if fields["metrics"]:
        st.markdown("**Metrics**")
        for m in fields["metrics"]:
            st.markdown(f"- `{m.name}` — {m.description or ', '.join(m.synonyms) or 'no synonyms'}")
            st.code(m.sql, language="sql")

    if fields["dimensions"]:
        st.markdown("**Dimensions**")
        df = to_frame(
            ["dimension", "description", "type", "sample values"],
            [
                (d.name, d.description, d.type, ", ".join(str(v) for v in d.sample_values))
                for d in fields["dimensions"]
            ],
        )
        st.dataframe(df, use_container_width=True, hide_index=True)


def main():
    import streamlit as st

    st.set_page_config(page_title="text2sql chat", page_icon="📊", layout="wide")
    st.title("📊 text2sql — ask your data")

    keys = list(DATASETS)
    with st.sidebar:
        st.subheader("Dataset")
        dataset_key = st.selectbox(
            "semantic model",
            keys,
            index=keys.index(DEFAULT_DATASET),
            format_func=lambda k: DATASETS[k].label,
        )
        view = st.radio("View", ["Chat", "Model Map"], horizontal=True)
        if view == "Chat" and st.button("🆕 New session", use_container_width=True):
            reset_session(st.session_state)
            st.rerun()

    # Switching datasets invalidates the prior chat (different columns/shape).
    if st.session_state.get("dataset") != dataset_key:
        st.session_state.dataset = dataset_key
        st.session_state.history = []

    ds = DATASETS[dataset_key]
    model = load_model(ds.model_path)  # cheap; needs no API key

    with st.sidebar:
        st.caption(f"semantic model: `{model.name}`")
        st.caption(f"planner model: `{get_model()}`")
        st.subheader("Try asking")
        for ex in ds.examples:
            st.markdown(f"- {ex}")

    if view == "Model Map":
        _render_model_map(st, model)
        return

    # ---- Chat view (needs the LLM planner) ----
    try:
        engine = st.cache_resource(build_engine)(dataset_key)
    except PlannerError as e:
        st.error(str(e))
        st.info("The **Model Map** view works without a key — switch to it in the sidebar.")
        return
    summarizer = st.cache_resource(build_summarizer)()
    store = st.cache_resource(build_trace_store)()

    if "history" not in st.session_state:
        st.session_state.history = []
    # One thread per browser session; persists across dataset switches (dataset is
    # recorded per turn instead), so a conversation keeps a stable id.
    if "thread_id" not in st.session_state:
        st.session_state.thread_id = uuid.uuid4().hex

    units = {m.name: m.unit for m in model.metrics}  # metric -> unit hint
    additive = {d.name: d.additive for d in model.dimensions}  # dim -> stackable?
    types = {d.name: d.type for d in model.dimensions}  # dim -> declared type (time?)

    for msg in st.session_state.history:
        with st.chat_message(msg["role"]):
            if msg["role"] == "user":
                st.markdown(msg["text"])
            else:
                _render_assistant(st, msg, units, additive, types)

    if prompt := st.chat_input(ds.placeholder):
        # Prior turns become the planner's short-term memory (before we append
        # the current prompt, so it isn't fed back to itself).
        history = recent_turns(st.session_state.history)
        st.session_state.history.append({"role": "user", "text": prompt})
        with st.chat_message("user"):
            st.markdown(prompt)
        with st.chat_message("assistant"):
            t0 = time.monotonic()
            with usage.collect() as calls:  # accrue every LLM call this turn makes
                try:
                    with st.spinner("Thinking…"):
                        result = engine.ask(prompt, history=history,
                                        thread_id=st.session_state.thread_id)
                        summary = safe_summarize(
                            summarizer, prompt, result.columns, result.rows
                        )
                    payload = {"role": "assistant", "result": result, "summary": summary}
                except EngineError as e:
                    payload = {"role": "assistant", "error": f"Sorry — I couldn't answer that: {e}"}
            latency_ms = (time.monotonic() - t0) * 1000
            payload["calls"] = calls  # for the token caption (live + on replay)
            _render_assistant(st, payload, units, additive, types)
            st.session_state.history.append(payload)
            record_turn(store, st.session_state.thread_id, dataset_key,
                        prompt, payload, calls, latency_ms)


if __name__ == "__main__":
    main()
