from __future__ import annotations

from .base import Dialect


class PostgresDialect(Dialect):
    """Postgres seam. Quoting/placeholder/limit are real so the compiler core
    produces correct Postgres text without modification. Date arithmetic differs
    enough that it is implemented here too; execution against a live Postgres is
    a later spec."""

    name = "postgres"

    def placeholder(self) -> str:
        return "%s"

    def current_date(self) -> str:
        return "CURRENT_DATE"

    def relative_date(self, n: int, unit: str, anchor_sql: str | None = None) -> str:
        anchor = anchor_sql if anchor_sql else "CURRENT_DATE"
        return f"(({anchor})::date - INTERVAL '{int(n)} {unit}s')"

    _TRUNC_UNITS = {"day", "week", "month", "quarter", "year"}

    def date_trunc(self, unit: str, col_sql: str) -> str:
        # Postgres date_trunc('week', ...) is ISO Monday. Cast to date (drop the time).
        if unit not in self._TRUNC_UNITS:
            raise ValueError(f"unsupported date_trunc unit: {unit!r}")
        return f"date_trunc('{unit}', ({col_sql})::timestamp)::date"

    _PART_KEYWORD = {"month": "month", "year": "year", "quarter": "quarter",
                     "day": "day", "dow": "dow"}

    def date_part(self, part: str, col_sql: str) -> str:
        kw = self._PART_KEYWORD.get(part)
        if kw is None:
            raise ValueError(f"unsupported date_part: {part!r}")
        return f"EXTRACT({kw} FROM ({col_sql})::timestamp)::int"
