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
    last_n_days: int


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
    time: TimeWindow | None = None
    order_by: list[OrderBy] = field(default_factory=list)
    limit: int | None = None

    # ---- serialization -------------------------------------------------
    @classmethod
    def from_dict(cls, d: dict) -> "SemanticQuery":
        time = None
        if d.get("time"):
            time = TimeWindow(
                field=d["time"]["field"],
                last_n_days=int(d["time"]["last_n_days"]),
            )
        return cls(
            metrics=list(d.get("metrics", [])),
            dimensions=list(d.get("dimensions", [])),
            filters=[
                Filter(f["field"], f["op"], f.get("value"))
                for f in d.get("filters", [])
            ],
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
        if self.time:
            out["time"] = {"field": self.time.field, "last_n_days": self.time.last_n_days}
        if self.order_by:
            out["order_by"] = [{"field": o.field, "dir": o.dir} for o in self.order_by]
        if self.limit is not None:
            out["limit"] = self.limit
        return out


# JSON schema used to constrain the Anthropic planner's tool output to a valid IR.
IR_JSON_SCHEMA = {
    "type": "object",
    "properties": {
        "metrics": {"type": "array", "items": {"type": "string"}},
        "dimensions": {"type": "array", "items": {"type": "string"}},
        "filters": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "field": {"type": "string"},
                    "op": {"type": "string", "enum": list(FILTER_OPS)},
                    "value": {},
                },
                "required": ["field", "op", "value"],
            },
        },
        "time": {
            "type": "object",
            "properties": {
                "field": {"type": "string"},
                "last_n_days": {"type": "integer"},
            },
            "required": ["field", "last_n_days"],
        },
        "order_by": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "field": {"type": "string"},
                    "dir": {"type": "string", "enum": ["asc", "desc"]},
                },
                "required": ["field"],
            },
        },
        "limit": {"type": "integer"},
    },
    "required": ["metrics", "dimensions"],
}
