"""Plot construction: turn a result set + a ChartSpec into Altair charts and the
display DataFrame. Streamlit-free on purpose — every function here returns a chart
object or a DataFrame, so the app layer only has to *place* them (st.altair_chart /
st.dataframe) and this module stays unit-testable.

Three sections: the dataviz palette + number formatting, frame *shaping* (compute),
and the chart *builders* (render). The chart *decision* (which chart for a query
shape) lives separately in charts.py.
"""

from __future__ import annotations

import pandas as pd

from text2sql.chat.charts import ChartSpec

# ---- dataviz reference palette + formatting -------------------------------
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

_PCT_TOKENS = ("pct", "percent", "%", "growth", "share", "rate")


def d3_format(unit: str | None) -> str:
    return _UNIT_D3.get(unit, ",")


def _md_safe(text: str) -> str:
    """Escape `$` so amounts like `$21,177.75` render as text, not LaTeX math,
    in st.markdown."""
    return text.replace("$", "\\$")


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


# ---- frame shaping (compute) ----------------------------------------------
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


# ---- chart builders (render) ----------------------------------------------
def horizontal_bar(df: pd.DataFrame, category: str, metric: str, sort="-x", fmt=","):
    """An Altair horizontal bar in the dataviz palette: single blue series,
    4px rounded data-ends, per-bar value labels (which replace the x-axis) and
    full-precision tooltip, all formatted with `fmt` (a d3 format string, e.g.
    "$,.2f"). `sort` orders the category axis: "-x" (default) sorts by this
    measure descending; pass an explicit list of category values to force a
    shared order across small multiples. Returns a chart for st.altair_chart."""
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


def line_chart(df: pd.DataFrame, x: str, value: str, color: str | None = None, fmt=","):
    """A time-series line in the dataviz palette: `value` over an ordered `x`, with
    one line per `color` category (palette + legend) when given, else a single blue
    line. Formatted y-axis + tooltip. Replaces st.line_chart so lines match the
    bars (palette, $/%, tooltips)."""
    import altair as alt

    tooltip = [alt.Tooltip(f"{x}:O")]
    if color:
        tooltip.append(alt.Tooltip(f"{color}:N", title=color.replace("_", " ")))
    tooltip.append(alt.Tooltip(f"{value}:Q", format=fmt))

    enc = {
        "x": alt.X(f"{x}:O", title=x.replace("_", " "),
                   axis=alt.Axis(labelColor=INK_SECONDARY)),
        "y": alt.Y(f"{value}:Q", title=None, scale=alt.Scale(zero=False),
                   axis=alt.Axis(format=fmt, labelColor=INK_MUTED)),
        "tooltip": tooltip,
    }
    if color:
        enc["color"] = alt.Color(f"{color}:N", title=None,
                                 scale=alt.Scale(range=PALETTE),
                                 legend=alt.Legend(orient="top"))
    return (
        alt.Chart(df).mark_line(point=True, color=SERIES_1)
        .encode(**enc).properties(height=320).configure_view(strokeWidth=0)
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
