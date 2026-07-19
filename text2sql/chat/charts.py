"""Deterministic chart selection from the Semantic Query IR and the result set.

Pure: no pandas, no Streamlit, no LLM. The rules pick a sensible default chart
from the *shape* of the query; the raw table is always shown by the app too, so
a conservative choice never loses information.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# A dimension is a time axis when its DECLARED type is temporal (Snowflake types
# time dimensions explicitly). Name tokens are a last-resort fallback used only
# when the caller doesn't pass declared types (e.g. some pure-unit tests).
TIME_TYPES = {"date", "time", "week", "month", "quarter", "year"}
_TIME_TOKENS = {"date", "day", "week", "month", "quarter", "year", "time"}

# Above this per-dimension distinct count a heatmap grid stops being readable, so a
# two-dimension result falls back to the table instead.
_HEATMAP_CAP = 25


@dataclass
class ChartSpec:
    kind: str  # "number" | "line" | "area" | "bar" | "scatter" | "heatmap" | "table"
    x: str | None = None
    y: list[str] = field(default_factory=list)
    series: str | None = None
    # "vertical" (default) | "horizontal" | "grouped" | "stacked" | "clustered".
    # A single-metric categorical bar is drawn horizontally and sorted by the metric
    # descending, so top-N reads top-to-bottom highest-first (labels stay legible).
    orientation: str = "vertical"


def is_time_like(name: str, types: dict | None = None) -> bool:
    if types is not None:  # declared-type driven (preferred)
        return types.get(name) in TIME_TYPES
    return any(tok in _TIME_TOKENS for tok in name.lower().split("_"))


def _distinct_count(name: str, columns: list[str], rows: list) -> int:
    if name not in columns:
        return 0
    i = columns.index(name)
    return len({r[i] for r in rows})


def _same_unit(metrics: list[str], units: dict | None) -> bool:
    """True iff every metric has the same known (non-None) unit — so they can
    honestly share one axis (a grouped bar) rather than needing small multiples."""
    if not units:
        return False
    seen = {units.get(m) for m in metrics}
    return len(seen) == 1 and None not in seen


def choose_chart(ir, columns: list[str], rows: list, units: dict | None = None,
                 additive: dict | None = None, types: dict | None = None) -> ChartSpec:
    # A period Comparison is wide (split_by + one metric column per period). It is
    # the SAME measure across periods, so it must never stack. When the periods
    # form a rolling time trend over a plain category (period is time, split_by is
    # not — e.g. week-over-week by product) a multi-series line reads best; else
    # the periods are compared side by side per bucket as a grouped bar (e.g.
    # Jan–Mar across 2025 vs 2026). The app melts the wide pivot to render either.
    if hasattr(ir, "period_field"):
        y = [c for c in columns if c != ir.split_by]
        if not rows or not y:
            return ChartSpec("table", y=y)
        if (
            is_time_like(ir.period_field, types)
            and not is_time_like(ir.split_by, types)
            and len(ir.periods) >= 3  # a trend needs several points; 2 -> grouped
        ):
            return ChartSpec("line", x=ir.period_field, y=[ir.metric], series=ir.split_by)
        # A time bucket (e.g. revenue vs expense per month) reads best as vertical
        # clustered columns, chronological; a categorical bucket (e.g. by store) stays
        # a horizontal grouped bar sorted by value.
        orientation = "clustered" if is_time_like(ir.split_by, types) else "grouped"
        return ChartSpec(
            "bar", x=ir.split_by, y=y, orientation=orientation, series=ir.period_field
        )

    metrics = [m for m in ir.metrics if m in columns]

    # No measures to plot, or nothing to plot against -> just the table.
    if not metrics or not rows:
        return ChartSpec("table", y=metrics)

    # Drop dimensions pinned to a single value by a filter (e.g. one product):
    # they carry no visual information.
    effective = [
        d for d in ir.dimensions
        if d in columns and _distinct_count(d, columns, rows) > 1
    ]

    if not effective:
        return ChartSpec("number", y=metrics)

    if len(effective) == 1:
        dim = effective[0]
        if is_time_like(dim, types):
            return ChartSpec("line", x=dim, y=metrics)
        # Two+ measures that share a unit (e.g. sales vs budget, both USD) can
        # sit on one axis -> a grouped bar. Different units (dollars vs a count)
        # must NOT share an axis -> small multiples (one horizontal bar each).
        # A single measure is just a horizontal bar. The spec carries every
        # measure in y; the render draws single / grouped / faceted accordingly.
        if len(metrics) > 1 and _same_unit(metrics, units):
            return ChartSpec("bar", x=dim, y=metrics, orientation="grouped")
        return ChartSpec("bar", x=dim, y=metrics, orientation="horizontal")

    if len(effective) == 2:
        time_dims = [d for d in effective if is_time_like(d, types)]
        if len(time_dims) == 1:
            x = time_dims[0]
            series = next(d for d in effective if d != x)
            # A single measure split by a categorical over time: if the split is
            # additive (parts sum to the period total) a stacked bar shows total +
            # composition. If it is contrasting (e.g. Revenue vs Expense, marked
            # additive:false in the model) stacking their sum is meaningless, so
            # compare with a multi-series line. Multiple measures also stay a line.
            if len(metrics) == 1 and (not additive or additive.get(series, True)):
                return ChartSpec("bar", x=x, y=metrics, series=series, orientation="stacked")
            return ChartSpec("line", x=x, y=metrics, series=series)
        # Two NON-time dimensions × one measure -> a heatmap (color-encoded matrix),
        # as long as both stay within a readable cardinality; else fall back to the
        # table. The lower-cardinality dim goes on x, the higher on y (a taller grid).
        if not time_dims and len(metrics) == 1:
            d0, d1 = effective
            n0, n1 = _distinct_count(d0, columns, rows), _distinct_count(d1, columns, rows)
            if 0 < n0 <= _HEATMAP_CAP and 0 < n1 <= _HEATMAP_CAP:
                x, ydim = (d0, d1) if n0 <= n1 else (d1, d0)
                return ChartSpec("heatmap", x=x, y=metrics, series=ydim)

    # Too complex to chart automatically -> show the table.
    return ChartSpec("table", y=metrics)


def compatible_charts(ir, columns: list[str], rows: list, units: dict | None = None,
                      additive: dict | None = None, types: dict | None = None) -> list[str]:
    """The chart kinds that make sense for this result shape, **recommended-first**
    (element 0 is `choose_chart`'s pick). Drives the UI chart-type switcher: the app
    auto-picks options[0] and lets the user override to another kind in the list.
    Pure — mirrors `choose_chart`."""
    spec = choose_chart(ir, columns, rows, units=units, additive=additive, types=types)
    k = spec.kind
    if k == "number":
        return ["number", "table"]
    if k == "table":
        return ["table"]
    if hasattr(ir, "period_field"):   # a period comparison renders as its line/bar
        return [k, "table"]
    if k == "line":
        opts = ["line", "area"]
        if not spec.series and len(spec.y) == 1:  # a single trend can also be a bar
            opts.append("bar")
        return opts + ["table"]
    if k == "heatmap":
        return ["heatmap", "table"]
    if k == "bar":
        if spec.orientation == "stacked":         # split over time -> line/area too
            return ["bar", "line", "area", "table"]
        if len(spec.y) == 2:                       # two measures -> correlation
            return ["bar", "scatter", "table"]
        return ["bar", "table"]
    return [k, "table"]
