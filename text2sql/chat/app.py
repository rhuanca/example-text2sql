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

from dataclasses import dataclass, field

import pandas as pd

from text2sql.config import get_api_key, get_model
from text2sql.db.seed import build_database as build_sales_db
from text2sql.db.seed_qbo import build_database as build_qbo_db
from text2sql.engine.dialects.sqlite import SqliteDialect
from text2sql.engine.engine import Engine, EngineError
from text2sql.engine.executor import SqliteExecutor
from text2sql.engine.planner import AnthropicPlanner, PlannerError
from text2sql.semantic.model import load_model
from text2sql.chat.charts import ChartSpec, choose_chart
from text2sql.chat.model_map import model_to_dot, table_fields
from text2sql.chat.summarizer import AnthropicSummarizer, MockSummarizer


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


# ---- chart styling (dataviz reference palette) ----------------------------
# Validated categorical slot 1 + ink tokens from the dataviz skill's reference
# palette. Marks carry the series hue; text/axis wear ink tokens, never the hue.
SERIES_1 = "#2a78d6"       # categorical slot 1 (blue), light surface
SERIES_2 = "#008300"       # categorical slot 2 (green), light surface
INK_SECONDARY = "#52514e"  # value/label ink
INK_MUTED = "#898781"      # axis/label muted
AXIS_LINE = "#c3c2b7"      # baseline / axis
# dataviz categorical palette (light), assigned in fixed order — never cycled.
PALETTE = [SERIES_1, SERIES_2, "#e87ba4", "#eda100", "#1baf7a", "#eb6834",
           "#4a3aa7", "#e34948"]

# d3 number formats per semantic-model unit (for axis ticks / labels / tooltips).
_UNIT_D3 = {"usd": "$,.2f", "count": ",", "percent": ".1%"}


def d3_format(unit: str | None) -> str:
    return _UNIT_D3.get(unit, ",")


def _md_safe(text: str) -> str:
    """Escape `$` so amounts like `$21,177.75` render as text, not LaTeX math,
    in st.markdown."""
    return text.replace("$", "\\$")


_PCT_TOKENS = ("pct", "percent", "%", "growth", "share", "rate")


def _percent_measure(name: str, units: dict) -> bool:
    """A measure is percent-like if the model tags its unit percent, or its column
    name suggests it (e.g. a derived `pct_change` from a window query)."""
    if units.get(name) == "percent":
        return True
    n = name.lower()
    return any(t in n for t in _PCT_TOKENS)


def _fmt_number(v, unit: str | None = None):
    """Format a value for display, honoring the metric's unit: usd -> `$4,983.00`,
    percent -> `12.3%`, otherwise thousands-comma (`4,983` / `159,033.65`).
    Non-numbers pass through unchanged."""
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return v
    if unit == "usd":
        return f"${v:,.2f}"
    if unit == "percent":
        return f"{v:.1%}"
    if isinstance(v, float) and not v.is_integer():
        return f"{v:,.2f}"
    return f"{int(v):,}"


# ---- pure helpers (importable / testable) ---------------------------------
def to_frame(columns: list[str], rows: list) -> pd.DataFrame:
    return pd.DataFrame(rows, columns=columns)


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


def chart_frame(spec: ChartSpec, columns: list[str], rows: list) -> pd.DataFrame:
    """Frame shaped for st.line_chart / st.bar_chart (indexed by x)."""
    df = to_frame(columns, rows)
    if spec.x and spec.series:
        return df.pivot(index=spec.x, columns=spec.series, values=spec.y[0])
    if spec.x:
        return df.set_index(spec.x)[spec.y]
    return df


def horizontal_bar(df: pd.DataFrame, category: str, metric: str, sort="-x", fmt=","):
    """An Altair horizontal bar in the dataviz palette: single blue series,
    4px rounded data-ends, per-bar value labels (which replace the x-axis) and
    full-precision tooltip, all formatted with `fmt` (a d3 format string, e.g.
    "$,.2f"). `sort` orders the category axis: "-x" (default) sorts by this
    measure descending; pass an explicit list of category values to force a
    shared order across small multiples. Lives here (not in the pure charts.py)
    because it needs Altair/pandas. Returns a chart for st.altair_chart."""
    import altair as alt

    y = alt.Y(
        f"{category}:N",
        sort=sort,  # by the measure descending, or an explicit shared order
        title=None,
        scale=alt.Scale(paddingInner=0.2, paddingOuter=0.2),  # air between bars
        axis=alt.Axis(labelColor=INK_SECONDARY, domainColor=AXIS_LINE, ticks=False),
    )
    # Direct labels carry the values, so the x-axis is hidden (dataviz rule:
    # direct labels before gridlines).
    x = alt.X(f"{metric}:Q", title=None, axis=None)
    base = alt.Chart(df)
    bars = base.mark_bar(color=SERIES_1, cornerRadiusEnd=4).encode(
        x=x,
        y=y,
        tooltip=[
            alt.Tooltip(f"{category}:N", title=category.replace("_", " ")),
            alt.Tooltip(f"{metric}:Q", title=metric.replace("_", " "), format=fmt),
        ],
    )
    labels = base.mark_text(align="left", dx=4, color=INK_SECONDARY).encode(
        x=x, y=y, text=alt.Text(f"{metric}:Q", format=fmt)
    )
    # Fixed 30px band per category (bar ~24px after padding) so bars stay a
    # readable thickness no matter the container width or number of rows.
    return (bars + labels).properties(height=alt.Step(30)).configure_view(strokeWidth=0)


def grouped_bar(df: pd.DataFrame, category: str, metrics: list[str], fmt=","):
    """An Altair grouped horizontal bar for measures that share a unit: bars are
    grouped per category, one color per measure, on a single formatted axis with
    a legend and tooltip. Category order follows the first measure descending."""
    import altair as alt

    order = df.sort_values(metrics[0], ascending=False)[category].tolist()
    long = df.melt(
        id_vars=[category], value_vars=metrics,
        var_name="measure", value_name="value",
    )
    return (
        alt.Chart(long)
        .mark_bar(cornerRadiusEnd=4)
        .encode(
            x=alt.X("value:Q", title=None,
                    axis=alt.Axis(format=fmt, labelColor=INK_MUTED, grid=False)),
            y=alt.Y(f"{category}:N", sort=order, title=None,
                    axis=alt.Axis(labelColor=INK_SECONDARY, domainColor=AXIS_LINE,
                                  ticks=False)),
            yOffset="measure:N",  # side-by-side bars within each category
            color=alt.Color("measure:N", title=None,
                            # fixed order: first measure -> slot 1 (blue), etc.
                            scale=alt.Scale(domain=metrics, range=[SERIES_1, SERIES_2]),
                            legend=alt.Legend(orient="top")),
            tooltip=[
                alt.Tooltip(f"{category}:N", title=category.replace("_", " ")),
                alt.Tooltip("measure:N", title="measure"),
                alt.Tooltip("value:Q", format=fmt),
            ],
        )
        .properties(height=alt.Step(16))  # per sub-bar; band fits both measures
        .configure_view(strokeWidth=0)
    )


def stacked_bar(df: pd.DataFrame, x: str, series: str, metric: str, fmt=","):
    """A vertical stacked bar: one measure split by a categorical over an ordered
    (time) axis. The segments sum to each period's total, so it shows the total and
    the composition at once. Colors follow the dataviz categorical palette."""
    import altair as alt

    return (
        alt.Chart(df)
        .mark_bar()
        .encode(
            x=alt.X(f"{x}:O", title=x.replace("_", " "),
                    axis=alt.Axis(labelColor=INK_SECONDARY)),
            y=alt.Y(f"{metric}:Q", title=None, stack="zero",
                    axis=alt.Axis(format=fmt, labelColor=INK_MUTED, grid=True)),
            color=alt.Color(f"{series}:N", title=None,
                            scale=alt.Scale(range=PALETTE),
                            legend=alt.Legend(orient="top")),
            tooltip=[
                alt.Tooltip(f"{x}:O"),
                alt.Tooltip(f"{series}:N", title=series.replace("_", " ")),
                alt.Tooltip(f"{metric}:Q", format=fmt),
            ],
        )
        .properties(height=340)
        .configure_view(strokeWidth=0)
    )


def comparison_long(comparison, columns: list[str], rows: list) -> pd.DataFrame:
    """Melt a wide period-comparison result to long rows (split_by, period, value).
    The columns after split_by are the pivoted `metric_<period>` columns in
    `comparison.periods` order, so we map them to the period value by POSITION
    (robust to underscores in the metric name). Pure/testable."""
    split = comparison.split_by
    period_cols = [c for c in columns if c != split]
    colmap = dict(zip(period_cols, comparison.periods))
    long = to_frame(columns, rows).melt(
        id_vars=[split], value_vars=period_cols, var_name="_col", value_name="value"
    )
    long["period"] = long["_col"].map(colmap)
    return long[[split, "period", "value"]]


def comparison_grouped_bar(long: pd.DataFrame, category: str, period_field: str,
                           periods: list, fmt=","):
    """Grouped horizontal bar for a period comparison: one colored bar per period
    within each category, side by side (never stacked), on one formatted axis.
    Categories ordered by total across periods; colors follow period order."""
    import altair as alt

    order = long.groupby(category)["value"].sum().sort_values(ascending=False).index.tolist()
    data = long.copy()
    data["period"] = data["period"].astype(str)
    domain = [str(p) for p in periods]
    return (
        alt.Chart(data)
        .mark_bar(cornerRadiusEnd=4)
        .encode(
            x=alt.X("value:Q", title=None,
                    axis=alt.Axis(format=fmt, labelColor=INK_MUTED, grid=False)),
            y=alt.Y(f"{category}:N", sort=order, title=None,
                    axis=alt.Axis(labelColor=INK_SECONDARY, domainColor=AXIS_LINE,
                                  ticks=False)),
            yOffset="period:N",  # side-by-side per period, never stacked
            color=alt.Color("period:N", title=period_field, sort=domain,
                            scale=alt.Scale(domain=domain, range=PALETTE[:len(periods)]),
                            legend=alt.Legend(orient="top")),
            tooltip=[
                alt.Tooltip(f"{category}:N", title=category.replace("_", " ")),
                alt.Tooltip("period:N", title=period_field),
                alt.Tooltip("value:Q", format=fmt),
            ],
        )
        .properties(height=alt.Step(16))
        .configure_view(strokeWidth=0)
    )


def line_panel(df: pd.DataFrame, x: str, metric: str, percent: bool = False):
    """A single-measure line panel for a small-multiples time series. A percent-like
    measure gets a `%`-formatted y-axis and a zero baseline, so a period-over-period
    change reads against zero (flat weeks sit on the line, drops fall below it)."""
    import altair as alt

    y_axis = alt.Axis(labelExpr="format(datum.value, '.1f') + '%'") if percent else alt.Axis()
    line = alt.Chart(df).mark_line(point=True, color=SERIES_1).encode(
        x=alt.X(f"{x}:O", title=x.replace("_", " ")),
        y=alt.Y(f"{metric}:Q", title=metric.replace("_", " "),
                scale=alt.Scale(zero=percent), axis=y_axis),
        tooltip=[alt.Tooltip(f"{x}:O"), alt.Tooltip(f"{metric}:Q")],
    )
    chart = line
    if percent:  # a zero reference line to anchor gains vs. drops
        zero = alt.Chart(df).mark_rule(color=AXIS_LINE).encode(y=alt.datum(0))
        chart = zero + line
    return chart.properties(height=180).configure_view(strokeWidth=0)


def _display_frame(result) -> pd.DataFrame:
    """The result table for display. For a period comparison, relabel the pivoted
    `metric_<p>` columns to a readable `metric (period_field=p)`."""
    df = to_frame(result.columns, result.rows)
    if hasattr(result.ir, "period_field"):
        cmp = result.ir
        period_cols = [c for c in result.columns if c != cmp.split_by]
        df = df.rename(columns={
            c: f"{cmp.metric} ({cmp.period_field}={p})"
            for c, p in zip(period_cols, cmp.periods)
        })
    return df


# ---- engine wiring --------------------------------------------------------
def build_engine(dataset_key: str = DEFAULT_DATASET) -> Engine:
    ds = DATASETS[dataset_key]
    if not os.path.exists(ds.db_path):
        ds.build_db(ds.db_path)
    model = load_model(ds.model_path)
    if not get_api_key():
        raise PlannerError("ANTHROPIC_API_KEY is not set — the chat app needs it.")
    planner = AnthropicPlanner()
    return Engine(model, planner, SqliteDialect(), SqliteExecutor(ds.db_path))


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
def _render_assistant(st, payload, units=None):
    units = units or {}
    if payload.get("error"):
        st.error(payload["error"])
        return
    result = payload["result"]
    st.markdown(_md_safe(payload["summary"]))

    spec = choose_chart(result.ir, result.columns, result.rows, units=units)
    if hasattr(result.ir, "period_field") and spec.kind in ("line", "bar"):
        # A period comparison: melt the wide pivot and render a trend line (week
        # over week) or a grouped bar (periods side by side) — never stacked.
        long = comparison_long(result.ir, result.columns, result.rows)
        fmt = d3_format(units.get(result.ir.metric))
        if spec.kind == "line":
            wide = long.pivot(index="period", columns=result.ir.split_by, values="value")
            st.line_chart(wide)
        else:
            st.altair_chart(
                comparison_grouped_bar(long, result.ir.split_by,
                                       result.ir.period_field, result.ir.periods, fmt=fmt),
                use_container_width=True,
            )
    elif spec.kind == "number" and result.rows:
        cols = st.columns(len(spec.y))
        for c, metric in zip(cols, spec.y):
            idx = result.columns.index(metric)
            c.metric(metric, _fmt_number(result.rows[0][idx], units.get(metric)))
    elif spec.kind == "line":
        metric_units = {units.get(m) for m in spec.y}
        same_scale = len(metric_units) == 1 and None not in metric_units
        if len(spec.y) > 1 and not spec.series and not same_scale:
            # measures of different (or unknown) scale — e.g. net sales AND a %
            # change — get one panel each so neither is squashed onto the other's
            # axis (a % near 0 vanishes next to sales in the hundreds).
            df = to_frame(result.columns, result.rows)
            for metric in spec.y:
                st.caption(metric.replace("_", " "))
                st.altair_chart(
                    line_panel(df, spec.x, metric, _percent_measure(metric, units)),
                    use_container_width=True,
                )
        else:
            st.line_chart(chart_frame(spec, result.columns, result.rows))
    elif spec.kind == "bar" and spec.orientation == "grouped":
        # Same-unit measures (e.g. sales vs budget) compared side by side on one
        # formatted axis.
        st.altair_chart(
            grouped_bar(to_frame(result.columns, result.rows), spec.x, spec.y,
                        fmt=d3_format(units.get(spec.y[0]))),
            use_container_width=True,
        )
    elif spec.kind == "bar" and spec.orientation == "horizontal":
        # One sorted horizontal bar per measure. With 2+ different-unit measures
        # (e.g. units and dollars) they render as small multiples — never mixed
        # on one axis. A shared product order (by the first measure) keeps rows
        # aligned so a product is easy to scan measure-to-measure.
        df = to_frame(result.columns, result.rows)
        order = df.sort_values(spec.y[0], ascending=False)[spec.x].tolist()
        for metric in spec.y:
            if len(spec.y) > 1:
                st.caption(metric.replace("_", " "))
            st.altair_chart(
                horizontal_bar(df, spec.x, metric, sort=order,
                               fmt=d3_format(units.get(metric))),
                use_container_width=True,
            )
    elif spec.kind == "bar" and spec.orientation == "stacked":
        # one measure split by a categorical over time -> stacked bar (total + mix)
        st.altair_chart(
            stacked_bar(to_frame(result.columns, result.rows), spec.x, spec.series,
                        spec.y[0], fmt=d3_format(units.get(spec.y[0]))),
            use_container_width=True,
        )
    elif spec.kind == "bar":
        st.bar_chart(chart_frame(spec, result.columns, result.rows))

    st.dataframe(_display_frame(result), use_container_width=True)
    with st.expander("Show SQL"):
        if result.semantic_sql:
            st.caption("Semantic SQL — written by the assistant")
            st.code(result.semantic_sql, language="sql")
            st.caption("Compiled SQL — what actually ran")
        st.code(result.sql, language="sql")


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
            st.markdown(f"- `{m.name}` — {', '.join(m.synonyms) or 'no synonyms'}")
            st.code(m.sql, language="sql")

    if fields["dimensions"]:
        st.markdown("**Dimensions**")
        df = to_frame(
            ["dimension", "column", "type", "sample values"],
            [
                (d.name, d.column, d.type, ", ".join(str(v) for v in d.sample_values))
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

    if "history" not in st.session_state:
        st.session_state.history = []

    units = {m.name: m.unit for m in model.metrics}  # metric -> unit hint

    for msg in st.session_state.history:
        with st.chat_message(msg["role"]):
            if msg["role"] == "user":
                st.markdown(msg["text"])
            else:
                _render_assistant(st, msg, units)

    if prompt := st.chat_input(ds.placeholder):
        # Prior turns become the planner's short-term memory (before we append
        # the current prompt, so it isn't fed back to itself).
        history = recent_turns(st.session_state.history)
        st.session_state.history.append({"role": "user", "text": prompt})
        with st.chat_message("user"):
            st.markdown(prompt)
        with st.chat_message("assistant"):
            try:
                with st.spinner("Thinking…"):
                    result = engine.ask(prompt, history=history)
                    summary = safe_summarize(
                        summarizer, prompt, result.columns, result.rows
                    )
                payload = {"role": "assistant", "result": result, "summary": summary}
            except EngineError as e:
                payload = {"role": "assistant", "error": f"Sorry — I couldn't answer that: {e}"}
            _render_assistant(st, payload, units)
            st.session_state.history.append(payload)


if __name__ == "__main__":
    main()
