"""Streamlit chat UI for the text-to-SQL engine.

Run it:
    uv run streamlit run text2sql/chat/app.py

Pure helpers (to_frame, chart_frame) are importable for tests; the Streamlit
rendering runs only under `streamlit run` (guarded by __main__).
"""

from __future__ import annotations

import os
import sys

# `streamlit run text2sql/chat/app.py` executes this file as a top-level script,
# so the package root is not on sys.path and relative imports would fail. Put the
# repo root on the path and use absolute imports so it works both as a script and
# when imported as text2sql.chat.app.
REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

import pandas as pd

from text2sql.config import get_api_key, get_model
from text2sql.db.seed import build_database
from text2sql.engine.dialects.sqlite import SqliteDialect
from text2sql.engine.engine import Engine, EngineError
from text2sql.engine.executor import SqliteExecutor
from text2sql.engine.planner import AnthropicPlanner, PlannerError
from text2sql.semantic.model import load_model
from text2sql.chat.charts import ChartSpec, choose_chart
from text2sql.chat.summarizer import AnthropicSummarizer, MockSummarizer

# Anchor data paths to the repo root so they resolve regardless of cwd.
MODEL_PATH = os.path.join(REPO_ROOT, "models", "sales.yml")
DB_PATH = os.path.join(REPO_ROOT, "demo.db")

EXAMPLES = [
    "How is Cappuccino performing week over week?",
    "What were total net sales by market?",
    "Budget vs actual by store",
]


# ---- pure helpers (importable / testable) ---------------------------------
def to_frame(columns: list[str], rows: list) -> pd.DataFrame:
    return pd.DataFrame(rows, columns=columns)


def chart_frame(spec: ChartSpec, columns: list[str], rows: list) -> pd.DataFrame:
    """Frame shaped for st.line_chart / st.bar_chart (indexed by x)."""
    df = to_frame(columns, rows)
    if spec.x and spec.series:
        return df.pivot(index=spec.x, columns=spec.series, values=spec.y[0])
    if spec.x:
        return df.set_index(spec.x)[spec.y]
    return df


# ---- engine wiring --------------------------------------------------------
def build_engine() -> Engine:
    if not os.path.exists(DB_PATH):
        build_database(DB_PATH)
    model = load_model(MODEL_PATH)
    if not get_api_key():
        raise PlannerError("ANTHROPIC_API_KEY is not set — the chat app needs it.")
    planner = AnthropicPlanner()
    return Engine(model, planner, SqliteDialect(), SqliteExecutor(DB_PATH))


def build_summarizer():
    return AnthropicSummarizer() if get_api_key() else MockSummarizer()


def safe_summarize(summarizer, question, columns, rows) -> str:
    try:
        text = summarizer.summarize(question, columns, rows)
        return text or _fallback_summary(rows)
    except Exception:
        return _fallback_summary(rows)


def _fallback_summary(rows) -> str:
    return f"Returned {len(rows)} row(s)." if rows else "No matching data found."


# ---- Streamlit rendering (only runs under `streamlit run`) -----------------
def _render_assistant(st, payload):
    if payload.get("error"):
        st.error(payload["error"])
        return
    result = payload["result"]
    st.markdown(payload["summary"])

    spec = choose_chart(result.ir, result.columns, result.rows)
    if spec.kind == "number" and result.rows:
        cols = st.columns(len(spec.y))
        for c, metric in zip(cols, spec.y):
            idx = result.columns.index(metric)
            c.metric(metric, result.rows[0][idx])
    elif spec.kind == "line":
        st.line_chart(chart_frame(spec, result.columns, result.rows))
    elif spec.kind == "bar":
        st.bar_chart(chart_frame(spec, result.columns, result.rows))

    st.dataframe(to_frame(result.columns, result.rows), use_container_width=True)
    with st.expander("Show SQL and query plan"):
        st.code(result.sql, language="sql")
        st.json(result.ir.to_dict())


def main():
    import streamlit as st

    st.set_page_config(page_title="text2sql chat", page_icon="📊", layout="wide")
    st.title("📊 text2sql — ask your data")

    engine = st.cache_resource(build_engine)()
    summarizer = st.cache_resource(build_summarizer)()

    with st.sidebar:
        st.subheader("Model")
        st.caption(f"semantic model: `{engine.model.name}`")
        st.caption(f"planner model: `{get_model()}`")
        st.subheader("Try asking")
        for ex in EXAMPLES:
            st.markdown(f"- {ex}")

    if "history" not in st.session_state:
        st.session_state.history = []

    for msg in st.session_state.history:
        with st.chat_message(msg["role"]):
            if msg["role"] == "user":
                st.markdown(msg["text"])
            else:
                _render_assistant(st, msg)

    if prompt := st.chat_input("Ask about sales, budget, or stores…"):
        st.session_state.history.append({"role": "user", "text": prompt})
        with st.chat_message("user"):
            st.markdown(prompt)
        with st.chat_message("assistant"):
            try:
                with st.spinner("Thinking…"):
                    result = engine.ask(prompt)
                    summary = safe_summarize(
                        summarizer, prompt, result.columns, result.rows
                    )
                payload = {"role": "assistant", "result": result, "summary": summary}
            except EngineError as e:
                payload = {"role": "assistant", "error": f"Sorry — I couldn't answer that: {e}"}
            _render_assistant(st, payload)
            st.session_state.history.append(payload)


if __name__ == "__main__":
    main()
