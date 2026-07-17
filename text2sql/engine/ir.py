"""Semantic Query IR.

The IR is the structured object the Query Planner (LLM) produces and the SQL
Compiler consumes. The planner never writes SQL; it only selects metrics,
group-by dimensions, filters, a time window, ordering, and a limit. Everything
downstream is deterministic.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# Filter operators supported in iteration one. Values are always bound as
# parameters, never interpolated into SQL text.
FILTER_OPS = ("=", "!=", "<", "<=", ">", ">=", "in", "not in", "like")


@dataclass
class Filter:
    field: str  # a dimension (or fact) name
    op: str
    value: object  # scalar, or list for in / not in

    def __post_init__(self):
        if self.op not in FILTER_OPS:
            raise ValueError(f"unsupported filter op: {self.op!r}")


@dataclass
class TimeWindow:
    field: str  # a date-typed dimension name
    last: int  # size of the window, in `unit`s
    unit: str = "day"  # day | week | month
    anchor: str = "data"  # "data" = latest date present; "today" = wall clock

    def __post_init__(self):
        if self.unit not in ("day", "week", "month"):
            raise ValueError(f"time unit must be day/week/month: {self.unit!r}")
        if self.anchor not in ("data", "today"):
            raise ValueError(f"time anchor must be data/today: {self.anchor!r}")


@dataclass
class OrderBy:
    field: str  # a metric or dimension name (must appear in the SELECT)
    dir: str = "asc"

    def __post_init__(self):
        self.dir = self.dir.lower()
        if self.dir not in ("asc", "desc"):
            raise ValueError(f"order dir must be asc/desc: {self.dir!r}")


@dataclass
class SemanticQuery:
    metrics: list[str] = field(default_factory=list)
    dimensions: list[str] = field(default_factory=list)
    filters: list[Filter] = field(default_factory=list)
    having: list[Filter] = field(default_factory=list)  # filters on aggregated metrics
    time: TimeWindow | None = None
    order_by: list[OrderBy] = field(default_factory=list)
    limit: int | None = None

    # ---- serialization -------------------------------------------------
    @classmethod
    def from_dict(cls, d: dict) -> "SemanticQuery":
        time = None
        if d.get("time"):
            t = d["time"]
            # accept the legacy `last_n_days` (day-grained) shape too
            last = t.get("last", t.get("last_n_days"))
            time = TimeWindow(
                field=t["field"],
                last=int(last),
                unit=t.get("unit", "day"),
                anchor=t.get("anchor", "data"),
            )
        mk_filters = lambda key: [
            Filter(f["field"], f["op"], f.get("value")) for f in d.get(key, [])
        ]
        return cls(
            metrics=list(d.get("metrics", [])),
            dimensions=list(d.get("dimensions", [])),
            filters=mk_filters("filters"),
            having=mk_filters("having"),
            time=time,
            order_by=[
                OrderBy(o["field"], o.get("dir", "asc"))
                for o in d.get("order_by", [])
            ],
            limit=d.get("limit"),
        )

    def to_dict(self) -> dict:
        out: dict = {"metrics": list(self.metrics), "dimensions": list(self.dimensions)}
        if self.filters:
            out["filters"] = [
                {"field": f.field, "op": f.op, "value": f.value} for f in self.filters
            ]
        if self.having:
            out["having"] = [
                {"field": f.field, "op": f.op, "value": f.value} for f in self.having
            ]
        if self.time:
            out["time"] = {
                "field": self.time.field, "last": self.time.last,
                "unit": self.time.unit, "anchor": self.time.anchor,
            }
        if self.order_by:
            out["order_by"] = [{"field": o.field, "dir": o.dir} for o in self.order_by]
        if self.limit is not None:
            out["limit"] = self.limit
        return out
