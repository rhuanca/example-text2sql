"""Semantic Query IR.

The IR is the normalized, structured form the semantic-SQL front-end produces —
by parsing and validating the LLM's SQL against the model (`semantic_sql.py`) —
and the SQL Compiler consumes. So the LLM authors SQL over the virtual model
table (metrics, dimensions, filters, a time window, ordering, a limit); that SQL
is lowered into this IR, and everything from the IR down is deterministic.
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
    # kind "trailing": the last `last` complete `unit`s up to today (e.g. last_period).
    # kind "to_date":  the current `unit` so far — start of period through today (YTD/MTD).
    last: int | None = None  # window size in `unit`s (trailing only)
    unit: str = "day"
    kind: str = "trailing"

    def __post_init__(self):
        if self.kind not in ("trailing", "to_date"):
            raise ValueError(f"time kind must be trailing/to_date: {self.kind!r}")
        if self.kind == "trailing":
            if self.unit not in ("day", "week", "month"):
                raise ValueError(f"trailing time unit must be day/week/month: {self.unit!r}")
            if not self.last or self.last < 1:
                raise ValueError(f"trailing window needs a positive `last`: {self.last!r}")
        else:  # to_date
            if self.unit not in ("day", "week", "month", "quarter", "year"):
                raise ValueError(f"to_date unit must be day/week/month/quarter/year: {self.unit!r}")


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
                last=int(last) if last is not None else None,
                unit=t.get("unit", "day"),
                kind=t.get("kind", "trailing"),
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
                "field": self.time.field,
                "unit": self.time.unit,
            }
            if self.time.last is not None:
                out["time"]["last"] = self.time.last
            if self.time.kind != "trailing":
                out["time"]["kind"] = self.time.kind
        if self.order_by:
            out["order_by"] = [{"field": o.field, "dir": o.dir} for o in self.order_by]
        if self.limit is not None:
            out["limit"] = self.limit
        return out
