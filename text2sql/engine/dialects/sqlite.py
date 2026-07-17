from __future__ import annotations

from .base import Dialect


class SqliteDialect(Dialect):
    name = "sqlite"

    def placeholder(self) -> str:
        return "?"

    def relative_date(self, amount: int, unit: str, anchor_sql: str | None = None) -> str:
        # sqlite has no 'weeks' modifier -> convert to days
        days = {"day": int(amount), "week": int(amount) * 7}.get(unit)
        modifier = f"-{days} days" if days is not None else f"-{int(amount)} months"
        anchor = anchor_sql or "'now'"
        return f"date({anchor}, '{modifier}')"
