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

    def relative_date(self, last_n_days: int) -> str:
        """SQL expression for (today - last_n_days), as a date literal."""
        raise NotImplementedError

    def limit_clause(self, n: int) -> str:
        return f"LIMIT {int(n)}"
