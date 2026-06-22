"""Deterministic chart selection from the Semantic Query IR and the result set.

Pure: no pandas, no Streamlit, no LLM. The rules pick a sensible default chart
from the *shape* of the query; the raw table is always shown by the app too, so
a conservative choice never loses information.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# Dimension names that represent an ordered/time axis -> line chart.
TIME_LIKE = {
    "date", "day", "week", "month", "quarter", "year",
    "iso_week", "iso_year", "iso_day_count", "week_start_date",
}


@dataclass
class ChartSpec:
    kind: str  # "number" | "line" | "bar" | "table"
    x: str | None = None
    y: list[str] = field(default_factory=list)
    series: str | None = None


def is_time_like(dim_name: str) -> bool:
    return dim_name in TIME_LIKE


def _distinct_count(name: str, columns: list[str], rows: list) -> int:
    if name not in columns:
        return 0
    i = columns.index(name)
    return len({r[i] for r in rows})


def choose_chart(ir, columns: list[str], rows: list) -> ChartSpec:
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
        kind = "line" if is_time_like(dim) else "bar"
        return ChartSpec(kind, x=dim, y=metrics)

    if len(effective) == 2:
        time_dims = [d for d in effective if is_time_like(d)]
        if len(time_dims) == 1:
            x = time_dims[0]
            series = next(d for d in effective if d != x)
            return ChartSpec("line", x=x, y=metrics, series=series)

    # Too complex to chart automatically -> show the table.
    return ChartSpec("table", y=metrics)
