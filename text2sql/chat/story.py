"""The 'narrate' layer: derive a data STORY (takeaway title, reference lines, point
annotations, focus emphasis) from a chosen chart + its data.

Pure — no LLM, no Streamlit — so it stays deterministically testable; the LLM prose
summary stays additive above the chart. Pipeline: decide (charts.py) -> narrate (here)
-> build (plots.py) -> place (app.py). Everything below is computed from
(columns, rows) + the model's unit/type hints. Storytelling principles: title = the
takeaway (Knaflic), annotate the peak/latest (Cox), reference lines (IBCS), grey the
context + colour the focus.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .plots import _fmt_number, month_label


@dataclass
class Reference:
    value: float
    label: str = ""
    role: str = "avg"  # avg | target | zero


@dataclass
class Annotation:
    x: object
    y: float
    text: str
    role: str = "latest"  # latest | peak | trough


@dataclass
class StorySpec:
    title: str | None = None
    subtitle: str | None = None
    references: list = field(default_factory=list)
    annotations: list = field(default_factory=list)
    emphasis: object | None = None  # focus category value (categorical bars)


def _pretty(name: str) -> str:
    return name.replace("_", " ")


def _idx(columns, name):
    return columns.index(name) if name in columns else None


def _xlabel(v, x, types):
    return month_label(v) if (types or {}).get(x) == "month" else v


def choose_story(ir, spec, columns, rows, units=None, types=None) -> "StorySpec | None":
    """Pure: (ir, ChartSpec, columns, rows, units, types) -> StorySpec, or None when
    there's no story to tell (a table, a single number, or an unsupported shape)."""
    if spec.kind in ("table", "number") or not rows:
        return None
    units = units or {}
    types = types or {}
    if hasattr(ir, "period_field"):
        return _comparison_story(ir, columns, rows, units)
    if spec.kind == "line" and len(spec.y) == 1 and not spec.series:
        return _trend_story(spec, columns, rows, units, types)
    if spec.kind == "bar" and spec.orientation == "horizontal" and len(spec.y) == 1:
        return _topn_story(spec, columns, rows, units)
    return None


def _trend_story(spec, columns, rows, units, types) -> "StorySpec | None":
    mi, xi = _idx(columns, spec.y[0]), _idx(columns, spec.x)
    if mi is None or xi is None:
        return None
    pts = [(r[xi], r[mi]) for r in rows if r[mi] is not None]
    if len(pts) < 3:  # a trend needs several points
        return None
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    unit = units.get(spec.y[0])
    fmt = lambda v: _fmt_number(v, unit)  # noqa: E731
    first, last, avg = ys[0], ys[-1], sum(ys) / len(ys)
    x0, x1 = _xlabel(xs[0], spec.x, types), _xlabel(xs[-1], spec.x, types)
    story = StorySpec()
    if first:
        pct = (last - first) / abs(first) * 100
        story.title = f"{_pretty(spec.y[0]).title()} {'grew' if pct >= 0 else 'fell'} {abs(pct):.0f}% ({x0} → {x1})"
    else:
        story.title = f"{_pretty(spec.y[0]).title()} over time"
    story.subtitle = f"{fmt(first)} → {fmt(last)}  ·  avg {fmt(avg)}"
    story.references.append(Reference(avg, f"avg {fmt(avg)}", "avg"))
    if any(v < 0 for v in ys):  # a signed metric (net income, % change) -> anchor at 0
        story.references.append(Reference(0, "", "zero"))
    story.annotations.append(Annotation(x1, last, fmt(last), "latest"))
    pk = max(range(len(ys)), key=lambda i: ys[i])
    if pk != len(ys) - 1 and max(ys) > min(ys):  # a distinct peak worth calling out
        story.annotations.append(
            Annotation(_xlabel(xs[pk], spec.x, types), ys[pk],
                       f"peak · {_xlabel(xs[pk], spec.x, types)}", "peak"))
    return story


def _topn_story(spec, columns, rows, units) -> "StorySpec | None":
    ci, mi = _idx(columns, spec.x), _idx(columns, spec.y[0])
    if ci is None or mi is None:
        return None
    vals = [(r[ci], r[mi]) for r in rows if r[mi] is not None]
    if len(vals) < 2:
        return None
    leader = max(vals, key=lambda t: t[1])
    total = sum(v for _, v in vals) or 1
    fmt = lambda v: _fmt_number(v, units.get(spec.y[0]))  # noqa: E731
    story = StorySpec()
    story.title = f"{leader[0]} leads — {fmt(leader[1])}"
    story.subtitle = f"{leader[1] / total * 100:.0f}% of the top {len(vals)}"
    story.emphasis = leader[0]
    return story


def _comparison_story(ir, columns, rows, units) -> "StorySpec | None":
    periods = list(ir.periods)
    if len(periods) != 2:
        return None
    ycols = [c for c in columns if c != ir.split_by]
    if len(ycols) < 2:
        return None
    sums = [sum((r[_idx(columns, c)] or 0) for r in rows) for c in ycols[:2]]
    if not sums[0]:
        return None
    fmt = lambda v: _fmt_number(v, units.get(ir.metric))  # noqa: E731
    delta = (sums[1] - sums[0]) / abs(sums[0]) * 100
    story = StorySpec()
    story.title = f"{periods[1]} vs {periods[0]}: {'+' if delta >= 0 else ''}{delta:.0f}%"
    # subtitle in the same period order as the title, each labelled so it can't be misread
    story.subtitle = f"{periods[1]} {fmt(sums[1])}  vs  {periods[0]} {fmt(sums[0])}"
    return story
