"""Dialect seam.

Everything dialect-specific (identifier quoting, parameter placeholders,
relative-date arithmetic, LIMIT syntax) goes through this interface so the
compiler core stays portable. Add a new database by adding a Dialect.
"""

from __future__ import annotations


class Dialect:
    name = "base"

    def quote_ident(self, ident: str) -> str:
        # double-quote identifiers; reject embedded quotes defensively
        if '"' in ident:
            raise ValueError(f"invalid identifier: {ident!r}")
        return f'"{ident}"'

    def placeholder(self) -> str:
        raise NotImplementedError

    def current_date(self) -> str:
        """The wall-clock 'today' as a date expression, valid as input to
        `date_trunc`/`relative_date`. Overridden per dialect (SQLite `'now'`,
        Postgres `CURRENT_DATE`, MySQL `CURDATE()`)."""
        raise NotImplementedError

    def relative_date(self, n: int, unit: str, anchor_sql: str | None = None) -> str:
        """SQL expression for (anchor - n*unit), unit in day|week|month.
        `anchor_sql` is a date expression; None means the dialect's own 'today'."""
        raise NotImplementedError

    def date_trunc(self, unit: str, col_sql: str) -> str:
        """Truncate a date/timestamp expression to a grain (day|week|month|quarter|
        year), returning a date. `week` truncates to the ISO Monday. This is how the
        model declares a calendar `month` / `week_start` bucket portably instead of
        baking dialect-specific SQL into the model."""
        raise NotImplementedError

    def date_part(self, part: str, col_sql: str) -> str:
        """Extract an integer part of a date (month|year|quarter|dow|day), e.g. a
        year-agnostic month-of-year 1-12."""
        raise NotImplementedError

    def limit_clause(self, n: int) -> str:
        return f"LIMIT {int(n)}"
