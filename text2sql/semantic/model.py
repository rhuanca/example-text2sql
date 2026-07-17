"""Semantic model: typed dataclasses + YAML loader + structural validation.

The semantic model is the contract between the natural-language layer and the
SQL compiler. It declares the logical tables, their relationships, and the
dimensions/facts/metrics the planner is allowed to reference. The compiler uses
it to turn a Semantic Query (IR) into SQL deterministically.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml


@dataclass
class Table:
    name: str  # logical name used everywhere (e.g. "sales")
    table: str  # physical table name in the database
    description: str = ""
    # Identity of the table; may be composite (list of columns). Declared here so
    # the columns count as known physical columns that joins can target.
    primary_key: list[str] = field(default_factory=list)
    # Other structural key columns: foreign/join keys, and keys shared across
    # fact tables for multi-base grouping. These are NOT measures — measures go
    # in `facts`. (Snowflake keeps join keys off the FACTS clause the same way.)
    keys: list[str] = field(default_factory=list)
    grain: str = ""


@dataclass
class Relationship:
    from_table: str
    to_table: str
    # One or more (from_column, to_column) equalities, ANDed together on join.
    # A single pair is the common case; multiple pairs express a composite join
    # (e.g. AccountID + Entity, where an id is only unique within a company).
    column_pairs: list[tuple[str, str]]

    @property
    def from_column(self) -> str:
        return self.column_pairs[0][0]

    @property
    def to_column(self) -> str:
        return self.column_pairs[0][1]


@dataclass
class Dimension:
    table: str
    name: str  # globally unique logical name
    description: str = ""
    column: str | None = None  # physical column (None for a derived dimension)
    type: str = "text"
    synonyms: list[str] = field(default_factory=list)
    sample_values: list = field(default_factory=list)
    # A derived dimension: a SQL expression over the table's columns instead of a
    # bare column, e.g. month = substr(date, 1, 7). Referenced unqualified (like a
    # metric's sql), so its columns must be unambiguous across the query's joins.
    expr: str | None = None
    # Whether this dimension's values partition a measure into parts that SUM to a
    # meaningful whole (True -> stackable, e.g. account) vs. contrasting facts that
    # do not (False -> charts compare instead of stack, e.g. Revenue/Expense).
    additive: bool = True


@dataclass
class Fact:
    table: str
    name: str
    column: str


@dataclass
class Metric:
    table: str
    name: str  # globally unique logical name
    sql: str  # aggregate expression over the table's physical columns
    description: str = ""
    synonyms: list[str] = field(default_factory=list)
    # Measurement unit, used to decide whether measures can share a chart axis
    # (same unit) and how to format their values. e.g. "usd", "count", "percent".
    unit: str | None = None
    # Tables the sql references beyond `table` (e.g. a metric that reads a joined
    # dimension column); the compiler joins them in. e.g. net_income reading
    # accounts.Classification declares joins: [accounts].
    joins: list[str] = field(default_factory=list)


@dataclass
class VerifiedQuery:
    """A curated question paired with known-good *semantic* SQL, fed to the planner
    as a few-shot example (Snowflake's verified_queries)."""

    question: str
    sql: str
    verified_by: str | None = None


@dataclass
class SemanticModel:
    name: str
    dialect: str
    tables: list[Table]
    relationships: list[Relationship]
    dimensions: list[Dimension]
    facts: list[Fact]
    metrics: list[Metric]
    verified_queries: list[VerifiedQuery] = field(default_factory=list)

    # ---- lookups -------------------------------------------------------
    def table(self, name: str) -> Table:
        for t in self.tables:
            if t.name == name:
                return t
        raise KeyError(f"unknown table: {name}")

    def metric(self, name: str) -> Metric:
        for m in self.metrics:
            if m.name == name:
                return m
        raise KeyError(f"unknown metric: {name}")

    def dimension(self, name: str) -> Dimension:
        for d in self.dimensions:
            if d.name == name:
                return d
        raise KeyError(f"unknown dimension: {name}")

    def field(self, name: str):
        """Return the dimension or metric with this logical name."""
        for d in self.dimensions:
            if d.name == name:
                return d
        for m in self.metrics:
            if m.name == name:
                return m
        raise KeyError(f"unknown field: {name}")

    def has_field(self, name: str) -> bool:
        try:
            self.field(name)
            return True
        except KeyError:
            return False

    def relationship_between(self, a: str, b: str) -> Relationship:
        """Direct relationship connecting logical tables a and b (either dir)."""
        for r in self.relationships:
            if {r.from_table, r.to_table} == {a, b}:
                return r
        raise KeyError(f"no relationship between {a} and {b}")

    def physical_columns(self, table_name: str) -> set[str]:
        """Physical columns known on a table: declared dims/facts, the primary
        key, and any relationship join columns that touch this table."""
        cols: set[str] = set()
        for d in self.dimensions:
            if d.table == table_name and d.column:  # skip derived (expr) dims
                cols.add(d.column)
        for f in self.facts:
            if f.table == table_name:
                cols.add(f.column)
        t = self.table(table_name)
        cols.update(t.primary_key)
        cols.update(t.keys)
        for r in self.relationships:
            if r.from_table == table_name:
                cols.update(fc for fc, _ in r.column_pairs)
            if r.to_table == table_name:
                cols.update(tc for _, tc in r.column_pairs)
        return cols


def _as_list(value) -> list[str]:
    """Normalize a scalar-or-list YAML field to a list (None -> [])."""
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    return list(value)


def _split_ref(ref: str) -> tuple[str, str]:
    if "." not in ref:
        raise ValueError(f"relationship endpoint must be 'table.column': {ref}")
    table, col = ref.split(".", 1)
    return table, col


def _build_relationship(r: dict) -> Relationship:
    """Parse one relationship entry. Single-column form:

        { from: txn.AccountID, to: accounts.Id }

    Composite (multi-column) form adds `also`, each an extra equality whose two
    sides reference the same two tables (either order):

        from: txn.AccountID
        to: accounts.Id
        also: ["txn.Entity = accounts.Entity"]
    """
    ft, fc = _split_ref(r["from"])
    tt, tc = _split_ref(r["to"])
    pairs = [(fc, tc)]
    for extra in r.get("also", []):
        if "=" not in extra:
            raise ValueError(f"'also' join must be 'table.col = table.col': {extra!r}")
        left, right = (s.strip() for s in extra.split("=", 1))
        lt, lc = _split_ref(left)
        rt, rc = _split_ref(right)
        if {lt, rt} != {ft, tt}:
            raise ValueError(
                f"'also' join {extra!r} must reference tables {ft!r} and {tt!r}"
            )
        # normalize so the pair is (from_column, to_column)
        pairs.append((lc, rc) if lt == ft else (rc, lc))
    return Relationship(from_table=ft, to_table=tt, column_pairs=pairs)


def load_model(path: str | Path) -> SemanticModel:
    data = yaml.safe_load(Path(path).read_text())
    return build_model(data)


def build_model(data: dict) -> SemanticModel:
    tables = [
        Table(
            name=t["name"],
            table=t.get("table", t["name"]),
            description=t.get("description", ""),
            primary_key=_as_list(t.get("primary_key")),
            keys=_as_list(t.get("keys")),
            grain=t.get("grain", ""),
        )
        for t in data.get("tables", [])
    ]

    relationships = [_build_relationship(r) for r in data.get("relationships", [])]

    dimensions = [
        Dimension(
            table=d["table"],
            name=d["name"],
            description=d.get("description", ""),
            column=d.get("column") or (None if d.get("expr") else d["name"]),
            type=d.get("type", "text"),
            synonyms=list(d.get("synonyms", [])),
            sample_values=list(d.get("sample_values", [])),
            expr=d.get("expr"),
            additive=d.get("additive", True),
        )
        for d in data.get("dimensions", [])
    ]

    facts = [
        Fact(table=f["table"], name=f["name"], column=f.get("column", f["name"]))
        for f in data.get("facts", [])
    ]

    metrics = [
        Metric(
            table=m["table"],
            name=m["name"],
            sql=m["sql"],
            description=m.get("description", ""),
            synonyms=list(m.get("synonyms", [])),
            unit=m.get("unit"),
            joins=list(m.get("joins", [])),
        )
        for m in data.get("metrics", [])
    ]

    verified_queries = [
        VerifiedQuery(question=q["question"], sql=q["sql"],
                      verified_by=q.get("verified_by"))
        for q in data.get("verified_queries", [])
    ]

    model = SemanticModel(
        name=data["name"],
        dialect=data.get("dialect", "sqlite"),
        tables=tables,
        relationships=relationships,
        dimensions=dimensions,
        facts=facts,
        metrics=metrics,
        verified_queries=verified_queries,
    )
    _validate(model)
    return model


def _validate(model: SemanticModel) -> None:
    table_names = {t.name for t in model.tables}
    if len(table_names) != len(model.tables):
        raise ValueError("duplicate table names")

    _no_dupes([d.name for d in model.dimensions], "dimension")
    _no_dupes([m.name for m in model.metrics], "metric")

    for d in model.dimensions:
        if d.table not in table_names:
            raise ValueError(f"dimension {d.name!r} references unknown table {d.table!r}")
    for f in model.facts:
        if f.table not in table_names:
            raise ValueError(f"fact {f.name!r} references unknown table {f.table!r}")
    for m in model.metrics:
        if m.table not in table_names:
            raise ValueError(f"metric {m.name!r} references unknown table {m.table!r}")

    for r in model.relationships:
        for tname in (r.from_table, r.to_table):
            if tname not in table_names:
                raise ValueError(f"relationship references unknown table {tname!r}")
        # every join column must be declared as a dim/fact/pk on its table
        for fc, tc in r.column_pairs:
            for tname, col in ((r.from_table, fc), (r.to_table, tc)):
                if col not in _declared_cols(model, tname):
                    raise ValueError(
                        f"relationship column {tname}.{col} is not declared on the table"
                    )


def _declared_cols(model: SemanticModel, table_name: str) -> set[str]:
    cols = {d.column for d in model.dimensions if d.table == table_name}
    cols |= {f.column for f in model.facts if f.table == table_name}
    t = model.table(table_name)
    cols |= set(t.primary_key)
    cols |= set(t.keys)
    return cols


def _no_dupes(names: list[str], kind: str) -> None:
    seen = set()
    for n in names:
        if n in seen:
            raise ValueError(f"duplicate {kind} name: {n}")
        seen.add(n)
