from __future__ import annotations

from .base import Dialect


class MysqlDialect(Dialect):
    """MySQL seam. Backtick identifiers, `%s` placeholders, and MySQL date syntax
    (DATE_SUB / DATE_FORMAT — MySQL has no DATE_TRUNC). Compiles correct MySQL text;
    live execution against a MySQL server is a later phase."""

    name = "mysql"

    def quote_ident(self, ident: str) -> str:
        if "`" in ident:
            raise ValueError(f"invalid identifier: {ident!r}")
        return f"`{ident}`"

    def placeholder(self) -> str:
        return "%s"

    def relative_date(self, n: int, unit: str, anchor_sql: str | None = None) -> str:
        anchor = anchor_sql if anchor_sql else "CURDATE()"
        # MySQL has native DAY/WEEK/MONTH intervals — no day-conversion needed.
        return f"DATE_SUB({anchor}, INTERVAL {int(n)} {unit.upper()})"

    def date_trunc(self, unit: str, col_sql: str) -> str:
        if unit == "week":  # ISO Monday: subtract WEEKDAY() (Mon=0)
            return f"DATE_SUB({col_sql}, INTERVAL WEEKDAY({col_sql}) DAY)"
        fmt = {
            "day": "%Y-%m-%d",
            "month": "%Y-%m-01",
            "year": "%Y-01-01",
        }.get(unit)
        if fmt is not None:
            return f"DATE_FORMAT({col_sql}, '{fmt}')"
        if unit == "quarter":  # first day of the quarter
            return (f"MAKEDATE(YEAR({col_sql}), 1) + "
                    f"INTERVAL (QUARTER({col_sql}) - 1) * 3 MONTH")
        raise ValueError(f"unsupported date_trunc unit: {unit!r}")

    _PART_FN = {"month": "MONTH", "year": "YEAR", "quarter": "QUARTER",
                "day": "DAYOFMONTH", "dow": "DAYOFWEEK"}

    def date_part(self, part: str, col_sql: str) -> str:
        fn = self._PART_FN.get(part)
        if fn is None:
            raise ValueError(f"unsupported date_part: {part!r}")
        return f"{fn}({col_sql})"
