from __future__ import annotations

from .base import Dialect


class SqliteDialect(Dialect):
    name = "sqlite"

    def placeholder(self) -> str:
        return "?"

    def current_date(self) -> str:
        return "'now'"

    def relative_date(self, n: int, unit: str, anchor_sql: str | None = None) -> str:
        # sqlite has no 'weeks' modifier -> convert to days
        days = {"day": int(n), "week": int(n) * 7}.get(unit)
        modifier = f"-{days} days" if days is not None else f"-{int(n)} months"
        anchor = anchor_sql or "'now'"
        return f"date({anchor}, '{modifier}')"

    # sqlite has no date_trunc; express each grain with date() modifiers.
    _TRUNC = {
        "day": "date({c})",
        "week": "date({c}, '-6 days', 'weekday 1')",   # ISO Monday
        "month": "date({c}, 'start of month')",
        "year": "date({c}, 'start of year')",
    }

    def date_trunc(self, unit: str, col_sql: str) -> str:
        if unit == "quarter":  # first day of the quarter, via the month number
            return (f"date({col_sql}, 'start of year', "
                    f"'+' || ((CAST(strftime('%m', {col_sql}) AS INTEGER) - 1) / 3 * 3) "
                    f"|| ' months')")
        tmpl = self._TRUNC.get(unit)
        if tmpl is None:
            raise ValueError(f"unsupported date_trunc unit: {unit!r}")
        return tmpl.format(c=col_sql)

    _PART = {"month": "%m", "year": "%Y", "day": "%d", "dow": "%w"}

    def date_part(self, part: str, col_sql: str) -> str:
        if part == "quarter":
            return f"((CAST(strftime('%m', {col_sql}) AS INTEGER) - 1) / 3 + 1)"
        fmt = self._PART.get(part)
        if fmt is None:
            raise ValueError(f"unsupported date_part: {part!r}")
        return f"CAST(strftime('{fmt}', {col_sql}) AS INTEGER)"
