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

    def relative_date(self, amount: int, unit: str, anchor_sql: str | None = None) -> str:
        """SQL expression for (anchor - amount*unit), unit in day|week|month.
        `anchor_sql` is a date expression (e.g. a `(SELECT MAX(col) FROM t)`
        subquery for data-anchoring); None means the dialect's own 'today'."""
        raise NotImplementedError

    def limit_clause(self, n: int) -> str:
        return f"LIMIT {int(n)}"
