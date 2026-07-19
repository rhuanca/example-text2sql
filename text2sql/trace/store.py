"""Local persistence for conversations, turns, and LLM token usage.

A small system of record we own — a separate sqlite file (never the read-only
demo databases) so turns can be analyzed with plain SQL. Writes are
**best-effort**: any failure is swallowed so persistence never breaks a user's
answer (mirroring the summarizer's degrade-gracefully contract). The schema is
created idempotently (`CREATE TABLE IF NOT EXISTS`) — the MVP "migration".

Stdlib sqlite3 only, but the write surface is deliberately narrow so it can move
to Postgres/MySQL through the engine's Dialect seam, exactly like the query path.
"""

from __future__ import annotations

import functools
import sqlite3

_SCHEMA = """
CREATE TABLE IF NOT EXISTS conversations (
    thread_id  TEXT PRIMARY KEY,
    dataset    TEXT,
    started_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS turns (
    turn_id      INTEGER PRIMARY KEY AUTOINCREMENT,
    thread_id    TEXT NOT NULL,
    dataset      TEXT,
    question     TEXT,
    rewritten    TEXT,
    semantic_sql TEXT,
    sql          TEXT,
    row_count    INTEGER,
    chart_kind   TEXT,
    error        TEXT,
    latency_ms   REAL,
    created_at   TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS llm_calls (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    turn_id       INTEGER NOT NULL,
    role          TEXT,
    model         TEXT,
    input_tokens  INTEGER,
    output_tokens INTEGER,
    ms            REAL
);
CREATE INDEX IF NOT EXISTS idx_turns_thread ON turns(thread_id);
CREATE INDEX IF NOT EXISTS idx_calls_turn ON llm_calls(turn_id);
"""


def _best_effort(fn):
    """Persistence must never break the answer path: swallow any exception and
    return None."""

    @functools.wraps(fn)
    def wrapper(self, *args, **kwargs):
        try:
            return fn(self, *args, **kwargs)
        except Exception:
            return None

    return wrapper


class TraceStore:
    def __init__(self, db_path: str):
        self.db_path = str(db_path)

    def _connect(self):
        return sqlite3.connect(self.db_path)

    @_best_effort
    def init(self) -> None:
        conn = self._connect()
        try:
            conn.executescript(_SCHEMA)
            conn.commit()
        finally:
            conn.close()

    @_best_effort
    def record_turn(
        self,
        *,
        thread_id: str,
        dataset: str | None = None,
        question: str | None = None,
        rewritten: str | None = None,
        semantic_sql: str | None = None,
        sql: str | None = None,
        row_count: int | None = None,
        chart_kind: str | None = None,
        error: str | None = None,
        latency_ms: float | None = None,
        calls=(),
    ) -> int | None:
        """Write a turn and its LLM calls in one transaction; ensure the parent
        conversation row exists. Returns the new turn_id (or None on failure)."""
        conn = self._connect()
        try:
            with conn:
                conn.execute(
                    "INSERT OR IGNORE INTO conversations(thread_id, dataset) VALUES (?, ?)",
                    (thread_id, dataset),
                )
                cur = conn.execute(
                    "INSERT INTO turns(thread_id, dataset, question, rewritten, "
                    "semantic_sql, sql, row_count, chart_kind, error, latency_ms) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (thread_id, dataset, question, rewritten, semantic_sql, sql,
                     row_count, chart_kind, error, latency_ms),
                )
                turn_id = cur.lastrowid
                conn.executemany(
                    "INSERT INTO llm_calls(turn_id, role, model, input_tokens, "
                    "output_tokens, ms) VALUES (?, ?, ?, ?, ?, ?)",
                    [(turn_id, c.role, c.model, c.input_tokens, c.output_tokens, c.ms)
                     for c in calls],
                )
            return turn_id
        finally:
            conn.close()
