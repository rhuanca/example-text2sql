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

    def relative_date(self, last_n_days: int) -> str:
        return f"(CURRENT_DATE - INTERVAL '{int(last_n_days)} days')"
