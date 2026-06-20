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
    primary_key: str | None = None
    grain: str = ""


@dataclass
class Relationship:
    from_table: str
    from_column: str
    to_table: str
    to_column: str


@dataclass
class Dimension:
    table: str
    name: str  # globally unique logical name
    column: str
    type: str = "text"
    synonyms: list[str] = field(default_factory=list)
    sample_values: list = field(default_factory=list)


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
    synonyms: list[str] = field(default_factory=list)


@dataclass
class Example:
    question: str
    ir: dict


@dataclass
class SemanticModel:
    name: str
    dialect: str
    tables: list[Table]
    relationships: list[Relationship]
    dimensions: list[Dimension]
    facts: list[Fact]
    metrics: list[Metric]
    examples: list[Example] = field(default_factory=list)

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
            if d.table == table_name:
                cols.add(d.column)
        for f in self.facts:
            if f.table == table_name:
                cols.add(f.column)
        t = self.table(table_name)
        if t.primary_key:
            cols.add(t.primary_key)
        for r in self.relationships:
            if r.from_table == table_name:
                cols.add(r.from_column)
            if r.to_table == table_name:
                cols.add(r.to_column)
        return cols


def _split_ref(ref: str) -> tuple[str, str]:
    if "." not in ref:
        raise ValueError(f"relationship endpoint must be 'table.column': {ref}")
    table, col = ref.split(".", 1)
    return table, col


def load_model(path: str | Path) -> SemanticModel:
    data = yaml.safe_load(Path(path).read_text())
    return build_model(data)


def build_model(data: dict) -> SemanticModel:
    tables = [
        Table(
            name=t["name"],
            table=t.get("table", t["name"]),
            description=t.get("description", ""),
            primary_key=t.get("primary_key"),
            grain=t.get("grain", ""),
        )
        for t in data.get("tables", [])
    ]

    relationships = []
    for r in data.get("relationships", []):
        ft, fc = _split_ref(r["from"])
        tt, tc = _split_ref(r["to"])
        relationships.append(Relationship(ft, fc, tt, tc))

    dimensions = [
        Dimension(
            table=d["table"],
            name=d["name"],
            column=d.get("column", d["name"]),
            type=d.get("type", "text"),
            synonyms=list(d.get("synonyms", [])),
            sample_values=list(d.get("sample_values", [])),
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
            synonyms=list(m.get("synonyms", [])),
        )
        for m in data.get("metrics", [])
    ]

    examples = [
        Example(question=e["question"], ir=e.get("ir", {}))
        for e in data.get("examples", [])
    ]

    model = SemanticModel(
        name=data["name"],
        dialect=data.get("dialect", "sqlite"),
        tables=tables,
        relationships=relationships,
        dimensions=dimensions,
        facts=facts,
        metrics=metrics,
        examples=examples,
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
        for tname, col in ((r.from_table, r.from_column), (r.to_table, r.to_column)):
            if tname not in table_names:
                raise ValueError(f"relationship references unknown table {tname!r}")
            # the column must be declared as a dim/fact/pk on that table
            if col not in _declared_cols(model, tname):
                raise ValueError(
                    f"relationship column {tname}.{col} is not declared on the table"
                )


def _declared_cols(model: SemanticModel, table_name: str) -> set[str]:
    cols = {d.column for d in model.dimensions if d.table == table_name}
    cols |= {f.column for f in model.facts if f.table == table_name}
    t = model.table(table_name)
    if t.primary_key:
        cols.add(t.primary_key)
    return cols


def _no_dupes(names: list[str], kind: str) -> None:
    seen = set()
    for n in names:
        if n in seen:
            raise ValueError(f"duplicate {kind} name: {n}")
        seen.add(n)
