"""Execute compiled SQL against SQLite over a read-only connection."""

from __future__ import annotations

import sqlite3
from pathlib import Path


class SqliteExecutor:
    def __init__(self, db_path: str | Path):
        self.db_path = str(db_path)

    def run(self, sql: str, params: list | None = None):
        """Return (columns, rows). Connection is opened read-only."""
        uri = f"file:{Path(self.db_path).resolve()}?mode=ro"
        conn = sqlite3.connect(uri, uri=True)
        try:
            cur = conn.execute(sql, params or [])
            columns = [c[0] for c in cur.description] if cur.description else []
            rows = cur.fetchall()
            return columns, rows
        finally:
            conn.close()
