from __future__ import annotations

from .base import Dialect


class SqliteDialect(Dialect):
    name = "sqlite"

    def placeholder(self) -> str:
        return "?"

    def relative_date(self, last_n_days: int) -> str:
        return f"date('now', '-{int(last_n_days)} days')"
