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
MUTED_FILL = "#d6dbe3"     # greyed-out (non-focus) bars/marks
# dataviz categorical palette (light), assigned in fixed order — never cycled.
PALETTE = [SERIES_1, SERIES_2, "#e87ba4", "#eda100", "#1baf7a", "#eb6834",
           "#4a3aa7", "#e34948"]

# d3 number formats per semantic-model unit (for axis ticks / labels / tooltips).
_UNIT_D3 = {"usd": "$,.2f", "count": ",", "percent": ".1%"}

_PCT_TOKENS = ("pct", "percent", "%", "growth", "share", "rate")

_FONT = "-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif"


def _register_theme():
    """One Vega-Lite theme carrying the shared chart styling — so branding lives in
    one place instead of being re-applied per builder. Enabled at import; its config
    is merged into every chart's spec at render time."""
    import altair as alt

    @alt.theme.register("text2sql", enable=True)
    def _theme():
        return alt.theme.ThemeConfig(
            config={
                "font": _FONT,
                "view": {"stroke": None},  # no chart border (was per-builder)
                "axis": {
                    "labelColor": INK_MUTED,
                    "titleColor": INK_SECONDARY,
                    "domainColor": AXIS_LINE,
                    "tickColor": AXIS_LINE,
                    "gridColor": "#ecebe4",
                    "labelFontSize": 11,
                    "titleFontSize": 12,
                },
                "legend": {
                    "orient": "top",
                    "labelColor": INK_SECONDARY,
                    "titleColor": INK_MUTED,
                    "symbolType": "square",
                },
                "title": {
                    "anchor": "start",
                    "color": INK_SECONDARY,
                    "subtitleColor": INK_MUTED,
                    "fontSize": 15,
                    "subtitleFontSize": 12,
                },
                "range": {"category": PALETTE},  # categorical color order
            }
        )


_register_theme()


def d3_format(unit: str | None) -> str:
    return _UNIT_D3.get(unit, ",")


def _pretty(name: str) -> str:
    """A column/field name as a readable axis/tooltip title (`net_sales` -> `net sales`)."""
    return name.replace("_", " ")


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


def needs_split(metrics: list, units: dict) -> bool:
    """Should a multi-measure time series render as one panel per metric (each on
    its own axis) rather than combined onto a shared axis? Yes unless every measure
    shares one *known* unit — measures of different or unknown scale would squash
    one another (e.g. a USD metric next to a percent change)."""
    scales = {units.get(m) for m in metrics}
    return len(metrics) > 1 and not (len(scales) == 1 and None not in scales)


_MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
           "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


def month_label(value):
    """Friendly label for a month dimension value. A calendar month — `"2026-04"` or
    the date-trunc form `"2026-04-01"` — becomes `"Apr 2026"`; a bare month-of-year
    `4` / `"4"` becomes `"Apr"` (no year — it spans years). Anything else (a year like
    `2026`, a week number, a non-month value) passes through."""
    s = str(value).strip()
    if len(s) == 10 and s[7:] == "-01":   # date_trunc('month') -> YYYY-MM-01
        s = s[:7]
    if len(s) == 7 and s[4] == "-" and s[:4].isdigit() and s[5:].isdigit():
        mm = int(s[5:])
        if 1 <= mm <= 12:
            return f"{_MONTHS[mm - 1]} {s[:4]}"
    elif s.isdigit():
        m = int(s)
        if 1 <= m <= 12:
            return _MONTHS[m - 1]
    return value


def _month_axis(df: pd.DataFrame, x: str, x_type: str | None):
    """If `x` is a month dimension, relabel its values to `month_label` and return
    an explicit chronological `sort` order (by the underlying value, so `"Apr 2026"`
    still orders correctly). Otherwise return the frame unchanged and `sort=None`."""
    if x_type != "month":
        return df, None
    order = [month_label(v) for v in sorted(set(df[x].tolist()))]
    df = df.copy()
    df[x] = df[x].map(month_label)
    return df, order


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


def bucket_long_tail(columns: list[str], rows: list, category: str, metric: str,
                     top_n: int = 12):
    """Cap a categorical result: keep the top-N categories by `metric` (descending)
    and fold the remainder into a single "Other" row (summed). Returns
    (columns, rows) unchanged when there are <= top_n categories. Pure/testable —
    keeps high-cardinality bar charts readable (best-practice: top-N + Other)."""
    ci, mi = columns.index(category), columns.index(metric)
    if len({r[ci] for r in rows}) <= top_n:
        return columns, rows
    ranked = sorted(rows, key=lambda r: (r[mi] is not None, r[mi]), reverse=True)
    keep = ranked[:top_n]
    rest = ranked[top_n:]
    other_total = sum((r[mi] or 0) for r in rest)
    # build the "Other" row: metric summed, category = "Other", other cols blank
    other = ["Other" if c == category else (other_total if c == metric else None)
             for c in columns]
    return columns, [list(r) for r in keep] + [other]


def _display_frame(result, types: dict | None = None) -> pd.DataFrame:
    """The result table for display. Month dimension columns render friendly
    (`"2026-04"` -> `"Apr 2026"`, a bare `4` -> `"Apr"`). A period comparison relabels
    the pivoted `metric_<p>` columns to `metric (period_field=p)` — unless the split is
    exclusive (each split value belongs to exactly one period, e.g. an account that is
    only Revenue OR Expense), where the wide pivot would be half zeros; then it shows
    tidy long rows (split_by, period_field, metric) instead."""
    types = types or {}
    cmp = result.ir if hasattr(result.ir, "period_field") else None
    if cmp is not None:
        period_cols = [c for c in result.columns if c != cmp.split_by]
        idx = [result.columns.index(c) for c in period_cols]
        present = lambda v: v not in (None, 0) and v == v  # not None/0/NaN  # noqa: E731
        if len(period_cols) >= 2 and all(
                sum(present(r[i]) for i in idx) <= 1 for r in result.rows):
            colmap = dict(zip(period_cols, cmp.periods))
            si = result.columns.index(cmp.split_by)
            rows = [(r[si], colmap[c], r[result.columns.index(c)])
                    for r in result.rows for c in period_cols
                    if present(r[result.columns.index(c)])]
            df = to_frame([cmp.split_by, cmp.period_field, cmp.metric], rows)
        else:
            df = to_frame(result.columns, result.rows).rename(columns={
                c: f"{cmp.metric} ({cmp.period_field}={p})"
                for c, p in zip(period_cols, cmp.periods)})
    else:
        df = to_frame(result.columns, result.rows)
    for col in df.columns:
        if types.get(col) == "month":
            df[col] = df[col].map(month_label)
    return df


# ---- chart builders (render) ----------------------------------------------
# ---- story overlays (the "narrate" layer renders here) ---------------------
# `story` is a duck-typed StorySpec (text2sql/chat/story.py) — kept import-free so
# plots.py stays a pure build layer with no dependency on the narrate layer.
def _titled(chart, story):
    """Attach a takeaway title + subtitle from the story, if any."""
    if story is None or not getattr(story, "title", None):
        return chart
    import altair as alt

    return chart.properties(title=alt.TitleParams(
        text=story.title, subtitle=story.subtitle or "", anchor="start",
        fontSize=15, subtitleColor=INK_MUTED, subtitleFontSize=12, offset=10))


def _ref_layers(story):
    """Reference lines (average / target / zero) as mark_rule layers behind the data."""
    if story is None:
        return []
    import altair as alt
    import pandas as pd

    out = []
    for r in story.references:
        dash = {"avg": [4, 3], "zero": [2, 2]}.get(r.role, [])
        out.append(alt.Chart(pd.DataFrame({"_y": [r.value]}))
                   .mark_rule(color=AXIS_LINE, strokeDash=dash).encode(y="_y:Q"))
    return out


def _ann_layers(story, x, value, xsort):
    """Point + text callouts (latest value, peak/trough) in front of the line."""
    if story is None:
        return []
    import altair as alt
    import pandas as pd

    out = []
    for a in story.annotations:
        d = pd.DataFrame([{x: a.x, value: a.y}])
        xe, ye = alt.X(f"{x}:O", sort=xsort), f"{value}:Q"
        if a.role == "latest":
            out.append(alt.Chart(d).mark_point(color=SERIES_1, filled=True, size=90)
                       .encode(x=xe, y=ye))
            out.append(alt.Chart(d).mark_text(align="right", dx=-8, dy=-11,
                                              color=SERIES_1, fontWeight="bold")
                       .encode(x=xe, y=ye, text=alt.value(a.text)))
        else:  # peak / trough
            out.append(alt.Chart(d).mark_text(align="center", dy=-12, color=INK_SECONDARY)
                       .encode(x=xe, y=ye, text=alt.value(a.text)))
    return out


def horizontal_bar(df: pd.DataFrame, category: str, metric: str, sort="-x", fmt=",",
                   story=None, mute=None):
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
    # All-positive bars carry direct value labels and hide the x-axis (dataviz rule:
    # direct labels before gridlines). A signed metric (net income) diverges around
    # zero, where direct labels collide with the bar / category labels — so it gets a
    # formatted x-axis + zero rule instead, and no direct labels.
    signed = bool((df[metric] < 0).any())
    x = alt.X(f"{metric}:Q", title=None,
              axis=alt.Axis(format=fmt, labelColor=INK_MUTED, grid=False) if signed else None)
    base = alt.Chart(df)
    # Emphasis: colour the story's focus (the leader) and grey the rest; or grey a
    # single muted category (the long-tail "Other" bucket) while the rest stay blue.
    import json
    focus = story.emphasis if (story is not None and getattr(story, "emphasis", None)
                               is not None) else None
    if focus is not None:
        color = alt.condition(f"datum[{json.dumps(category)}] == {json.dumps(focus)}",
                              alt.value(SERIES_1), alt.value(MUTED_FILL))
    elif mute is not None:
        color = alt.condition(f"datum[{json.dumps(category)}] == {json.dumps(mute)}",
                              alt.value(MUTED_FILL), alt.value(SERIES_1))
    else:
        color = alt.value(SERIES_1)
    bars = base.mark_bar(cornerRadiusEnd=4).encode(
        x=x,
        y=y,
        color=color,
        tooltip=[
            alt.Tooltip(f"{category}:N", title=_pretty(category)),
            alt.Tooltip(f"{metric}:Q", title=_pretty(metric), format=fmt),
        ],
    )
    if signed:  # diverging: a zero baseline for reference, values read from the axis
        content = base.mark_rule(color=AXIS_LINE).encode(x=alt.datum(0)) + bars
    else:  # direct value labels just past each bar tip
        content = bars + base.mark_text(align="left", dx=4, color=INK_SECONDARY).encode(
            x=x, y=y, text=alt.Text(f"{metric}:Q", format=fmt))
    # Fixed 30px band per category (bar ~24px after padding) so bars stay a
    # readable thickness no matter the container width or number of rows.
    return _titled(content.properties(height=alt.Step(30)), story)


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
                alt.Tooltip(f"{category}:N", title=_pretty(category)),
                alt.Tooltip("measure:N", title="measure"),
                alt.Tooltip("value:Q", format=fmt),
            ],
        )
        .properties(height=alt.Step(16))  # per sub-bar; band fits both measures
    )


def stacked_bar(df: pd.DataFrame, x: str, color: str, metric: str, fmt=",",
                x_type: str | None = None):
    """A vertical stacked bar: one measure split by a categorical over an ordered
    (time) axis. The segments sum to each period's total, so it shows the total and
    the composition at once. Colors follow the dataviz categorical palette."""
    import altair as alt

    df, xsort = _month_axis(df, x, x_type)
    return (
        alt.Chart(df)
        .mark_bar()
        .encode(
            x=alt.X(f"{x}:O", title=_pretty(x), sort=xsort,
                    axis=alt.Axis(labelColor=INK_SECONDARY)),
            y=alt.Y(f"{metric}:Q", title=None, stack="zero",
                    axis=alt.Axis(format=fmt, labelColor=INK_MUTED, grid=True)),
            color=alt.Color(f"{color}:N", title=None,
                            scale=alt.Scale(range=PALETTE),
                            legend=alt.Legend(orient="top")),
            tooltip=[
                alt.Tooltip(f"{x}:O"),
                alt.Tooltip(f"{color}:N", title=_pretty(color)),
                alt.Tooltip(f"{metric}:Q", format=fmt),
            ],
        )
        .properties(height=340)
            )


def comparison_grouped_bar(long: pd.DataFrame, category: str, period_field: str,
                           periods: list, fmt=",", story=None):
    """Grouped horizontal bar for a period comparison: one colored bar per period
    within each category, side by side (never stacked), on one formatted axis.
    Categories ordered by total across periods; colors follow period order."""
    import altair as alt

    order = long.groupby(category)["value"].sum().sort_values(ascending=False).index.tolist()
    data = long.copy()
    data["period"] = data["period"].astype(str)
    domain = [str(p) for p in periods]
    return _titled(
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
                alt.Tooltip(f"{category}:N", title=_pretty(category)),
                alt.Tooltip("period:N", title=period_field),
                alt.Tooltip("value:Q", format=fmt),
            ],
        )
        .properties(height=alt.Step(16)),
        story,
    )


def vertical_grouped_bar(long: pd.DataFrame, category: str, period_field: str,
                         periods: list, fmt=",", x_type: str | None = None, story=None):
    """Vertical clustered columns for a period comparison over a time bucket: one
    colored bar per period within each time category, side by side (never stacked),
    ordered chronologically along the x-axis. Month categories render friendly
    ("2026-04" -> "Apr 2026"). Contrast with `comparison_grouped_bar`, which is
    horizontal and value-sorted for a categorical bucket."""
    import altair as alt

    long, xsort = _month_axis(long, category, x_type)
    data = long.copy()
    data["period"] = data["period"].astype(str)
    domain = [str(p) for p in periods]
    return _titled(
        alt.Chart(data)
        .mark_bar(cornerRadiusEnd=4)
        .encode(
            x=alt.X(f"{category}:O", title=_pretty(category), sort=xsort,
                    axis=alt.Axis(labelColor=INK_SECONDARY, domainColor=AXIS_LINE,
                                  ticks=False)),
            xOffset="period:N",  # side-by-side per period, never stacked
            y=alt.Y("value:Q", title=None,
                    axis=alt.Axis(format=fmt, labelColor=INK_MUTED, grid=True)),
            color=alt.Color("period:N", title=period_field, sort=domain,
                            scale=alt.Scale(domain=domain, range=PALETTE[:len(periods)]),
                            legend=alt.Legend(orient="top")),
            tooltip=[
                alt.Tooltip(f"{category}:N", title=_pretty(category)),
                alt.Tooltip("period:N", title=period_field),
                alt.Tooltip("value:Q", format=fmt),
            ],
        )
        .properties(height=340)
        , story)


def line_chart(df: pd.DataFrame, x: str, metric: str, color: str | None = None, fmt=",",
               x_type: str | None = None, story=None):
    """A time-series line in the dataviz palette: `metric` over an ordered `x`, with
    one line per `color` category (palette + legend) when given, else a single blue
    line. A single-series line also carries the story's takeaway title, average/zero
    reference lines, and latest/peak callouts."""
    import altair as alt

    df, xsort = _month_axis(df, x, x_type)
    tooltip = [alt.Tooltip(f"{x}:O")]
    if color:
        tooltip.append(alt.Tooltip(f"{color}:N", title=_pretty(color)))
    tooltip.append(alt.Tooltip(f"{metric}:Q", format=fmt))

    enc = {
        "x": alt.X(f"{x}:O", title=_pretty(x), sort=xsort,
                   axis=alt.Axis(labelColor=INK_SECONDARY)),
        "y": alt.Y(f"{metric}:Q", title=None, scale=alt.Scale(zero=False),
                   axis=alt.Axis(format=fmt, labelColor=INK_MUTED)),
        "tooltip": tooltip,
    }
    if color:
        enc["color"] = alt.Color(f"{color}:N", title=None,
                                 scale=alt.Scale(range=PALETTE),
                                 legend=alt.Legend(orient="top"))
    line = alt.Chart(df).mark_line(point=True, color=SERIES_1).encode(**enc)
    # A single-series trend gets story overlays: reference lines behind, callouts front.
    if story is not None and not color:
        chart = alt.layer(*_ref_layers(story), line, *_ann_layers(story, x, metric, xsort))
    else:
        chart = line
    return _titled(chart.properties(height=320), story)


def line_panel(df: pd.DataFrame, x: str, metric: str, percent: bool = False,
               x_type: str | None = None):
    """A single-measure line panel for a small-multiples time series. A percent-like
    measure gets a `%`-formatted y-axis and a zero baseline, so a period-over-period
    change reads against zero (flat weeks sit on the line, drops fall below it)."""
    import altair as alt

    df, xsort = _month_axis(df, x, x_type)
    y_axis = alt.Axis(labelExpr="format(datum.value, '.1f') + '%'") if percent else alt.Axis()
    line = alt.Chart(df).mark_line(point=True, color=SERIES_1).encode(
        x=alt.X(f"{x}:O", title=_pretty(x), sort=xsort),
        y=alt.Y(f"{metric}:Q", title=_pretty(metric),
                scale=alt.Scale(zero=percent), axis=y_axis),
        tooltip=[alt.Tooltip(f"{x}:O"), alt.Tooltip(f"{metric}:Q")],
    )
    chart = line
    if percent:  # a zero reference line to anchor gains vs. drops
        zero = alt.Chart(df).mark_rule(color=AXIS_LINE).encode(y=alt.datum(0))
        chart = zero + line
    return chart.properties(height=180)


def area_chart(df: pd.DataFrame, x: str, metric: str, color: str | None = None, fmt=",",
               x_type: str | None = None, story=None, percent: bool = False):
    """A filled time-series area (a line's sibling, emphasizing magnitude/volume):
    a single blue area anchored at zero with a solid top line and the story overlays,
    or a stacked area per `color` category. Chosen via the chart-type switcher for a
    time-series shape (the default stays a line). A percent-like measure gets a
    `%`-formatted y-axis and a zero baseline (as in `line_panel`), so it isn't
    squashed when shown as its own small-multiple panel."""
    import altair as alt

    df, xsort = _month_axis(df, x, x_type)
    tooltip = [alt.Tooltip(f"{x}:O")]
    if color:
        tooltip.append(alt.Tooltip(f"{color}:N", title=_pretty(color)))
    tooltip.append(alt.Tooltip(f"{metric}:Q", format=fmt))
    y_kw = dict(title=None, stack="zero" if color else None,
                axis=alt.Axis(labelExpr="format(datum.value, '.1f') + '%'", labelColor=INK_MUTED)
                if percent else alt.Axis(format=fmt, labelColor=INK_MUTED))
    if percent:
        y_kw["scale"] = alt.Scale(zero=True)
    enc = {
        "x": alt.X(f"{x}:O", title=_pretty(x), sort=xsort,
                   axis=alt.Axis(labelColor=INK_SECONDARY)),
        "y": alt.Y(f"{metric}:Q", **y_kw),
        "tooltip": tooltip,
    }
    if color:
        enc["color"] = alt.Color(f"{color}:N", title=None,
                                 scale=alt.Scale(range=PALETTE),
                                 legend=alt.Legend(orient="top"))
        return _titled(alt.Chart(df).mark_area(opacity=0.85).encode(**enc)
                       .properties(height=320), story)
    area = alt.Chart(df).mark_area(
        line={"color": SERIES_1}, color=SERIES_1, opacity=0.25).encode(**enc)
    if story is not None:  # same reference/callout overlays as the line
        area = alt.layer(*_ref_layers(story), area, *_ann_layers(story, x, metric, xsort))
    return _titled(area.properties(height=320), story)


def faceted_line(df: pd.DataFrame, x: str, metric: str, color: str, facet: str,
                 fmt=",", x_type: str | None = None):
    """Small multiples for a 1-time + 2-categorical result: the metric over an ordered
    `x` (time), one line per `color` category, in a panel per `facet` value — a "side by
    side" comparison (e.g. Revenue vs Expense panels, accounts over weeks). The y-axis is
    shared across panels so their magnitudes are directly comparable."""
    import altair as alt

    df, xsort = _month_axis(df, x, x_type)
    panel = alt.Chart(df).mark_line(point=True).encode(
        x=alt.X(f"{x}:O", title=None, sort=xsort),
        y=alt.Y(f"{metric}:Q", title=None, axis=alt.Axis(format=fmt)),
        color=alt.Color(f"{color}:N", title=None, scale=alt.Scale(range=PALETTE),
                        legend=alt.Legend(orient="top")),
        tooltip=[alt.Tooltip(f"{facet}:N", title=_pretty(facet)),
                 alt.Tooltip(f"{color}:N", title=_pretty(color)),
                 alt.Tooltip(f"{x}:O"),
                 alt.Tooltip(f"{metric}:Q", format=fmt)],
    ).properties(height=200, width=300)
    return panel.facet(
        alt.Facet(f"{facet}:N", title=None,
                  header=alt.Header(labelFontSize=13, labelFontWeight="bold",
                                    labelColor=INK_SECONDARY)),
        columns=2,
    )


def scatter_chart(df: pd.DataFrame, x_metric: str, y_metric: str, label: str | None = None,
                  fmt_x=",", fmt_y=","):
    """A scatter for two-measure correlation: each point is a `label` category placed
    by (`x_metric`, `y_metric`). Points carry a full tooltip; neither axis is zeroed so
    the cloud fills the frame."""
    import altair as alt

    tooltip = []
    if label:
        tooltip.append(alt.Tooltip(f"{label}:N", title=_pretty(label)))
    tooltip += [alt.Tooltip(f"{x_metric}:Q", title=_pretty(x_metric), format=fmt_x),
                alt.Tooltip(f"{y_metric}:Q", title=_pretty(y_metric), format=fmt_y)]
    return alt.Chart(df).mark_circle(size=110, color=SERIES_1, opacity=0.75).encode(
        x=alt.X(f"{x_metric}:Q", title=_pretty(x_metric),
                scale=alt.Scale(zero=False), axis=alt.Axis(format=fmt_x)),
        y=alt.Y(f"{y_metric}:Q", title=_pretty(y_metric),
                scale=alt.Scale(zero=False), axis=alt.Axis(format=fmt_y)),
        tooltip=tooltip,
    ).properties(height=340)


def heatmap(df: pd.DataFrame, x: str, y: str, metric: str, fmt=","):
    """A matrix heatmap for two categorical dimensions × one measure: `x`/`y` cells
    colored by `metric` on a sequential single-hue scale (white -> blue, per dataviz —
    never a rainbow). Value labels are drawn only when the grid is small enough to stay
    legible; a full tooltip is always present."""
    import altair as alt

    base = alt.Chart(df).encode(
        x=alt.X(f"{x}:N", title=_pretty(x)),
        y=alt.Y(f"{y}:N", title=_pretty(y)),
    )
    rects = base.mark_rect().encode(
        color=alt.Color(f"{metric}:Q", title=_pretty(metric),
                        scale=alt.Scale(range=["#f2f6fc", SERIES_1]),
                        legend=alt.Legend(orient="right", format=fmt)),
        tooltip=[
            alt.Tooltip(f"{x}:N", title=_pretty(x)),
            alt.Tooltip(f"{y}:N", title=_pretty(y)),
            alt.Tooltip(f"{metric}:Q", title=_pretty(metric), format=fmt),
        ],
    )
    n_cells = df[x].nunique() * df[y].nunique()
    if n_cells <= 60:  # label cells only while they stay readable
        labels = base.mark_text(baseline="middle", fontSize=10, color=INK_SECONDARY).encode(
            text=alt.Text(f"{metric}:Q", format=fmt))
        return (rects + labels).properties(height=alt.Step(34))
    return rects.properties(height=alt.Step(34))