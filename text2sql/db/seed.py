"""Build a SQLite database with small, fixed sample data for the product_sales
model. Deterministic (no randomness) so tests are reproducible.

Run as a module to create ./demo.db:
    uv run python -m text2sql.db.seed
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

DEFAULT_DB = "demo.db"

SCHEMA = """
CREATE TABLE storeinfo (
    fc_number           TEXT PRIMARY KEY,
    market              TEXT,
    region              TEXT,
    state               TEXT,
    corporate_franchise TEXT,
    lifecycle_stage     TEXT,
    open_date           TEXT
);

CREATE TABLE sales (
    transaction_id      TEXT,
    fc_number           TEXT,
    date                TEXT,
    iso_week            INTEGER,
    iso_year            INTEGER,
    product_name        TEXT,
    category_name       TEXT,
    purchase_location   TEXT,
    item_net_sales      REAL,
    quantity            INTEGER,
    transaction_deleted INTEGER,
    transaction_return  INTEGER
);

CREATE TABLE budget (
    fc_number        TEXT,
    date             TEXT,
    iso_week         INTEGER,
    iso_year         INTEGER,
    budget_net_sales REAL
);
"""

STOREINFO = [
    ("FC5063", "Houston", "South Texas", "TX", "Corporate", "Open", "2020-01-15"),
    ("FC5100", "Dallas", "North Texas", "TX", "Franchise", "Open", "2022-06-01"),
]

# (txn, fc, date, week, year, product, category, location, net, qty, deleted, return)
SALES = [
    # ---- FC5063, ISO week 10 (2026-03-02) ----
    ("T1", "FC5063", "2026-03-02", 10, 2026, "Dozen Glazed", "Donuts", "At Shop", 12.49, 1, 0, 0),
    ("T1", "FC5063", "2026-03-02", 10, 2026, "Coffee - Regular", "Beverages", "At Shop", 2.50, 1, 0, 0),
    ("T2", "FC5063", "2026-03-02", 10, 2026, "Dozen Glazed", "Donuts", "Online", 24.98, 2, 0, 0),
    ("T3", "FC5063", "2026-03-02", 10, 2026, "Kolache", "Kolaches", "At Shop", 9.00, 3, 0, 0),
    ("T4", "FC5063", "2026-03-02", 10, 2026, "Dozen Glazed", "Donuts", "At Shop", 12.49, 1, 1, 0),  # deleted
    ("T5", "FC5063", "2026-03-02", 10, 2026, "Dozen Glazed", "Donuts", "At Shop", -12.49, 1, 0, 1),  # return
    # ---- FC5063, ISO week 11 (2026-03-09) ----
    ("T6", "FC5063", "2026-03-09", 11, 2026, "Dozen Glazed", "Donuts", "At Shop", 12.49, 1, 0, 0),
    ("T7", "FC5063", "2026-03-09", 11, 2026, "Dozen Glazed", "Donuts", "Online", 37.47, 3, 0, 0),
    ("T8", "FC5063", "2026-03-09", 11, 2026, "Coffee - Regular", "Beverages", "At Shop", 5.00, 2, 0, 0),
    # ---- FC5100 ----
    ("T9", "FC5100", "2026-03-02", 10, 2026, "Dozen Glazed", "Donuts", "At Shop", 12.49, 1, 0, 0),
    ("T10", "FC5100", "2026-03-09", 11, 2026, "Kolache", "Kolaches", "At Shop", 6.00, 2, 0, 0),
]

BUDGET = [
    ("FC5063", "2026-03-02", 10, 2026, 3500.00),
    ("FC5063", "2026-03-09", 11, 2026, 3600.00),
    ("FC5100", "2026-03-02", 10, 2026, 2800.00),
    ("FC5100", "2026-03-09", 11, 2026, 2900.00),
]


def build_database(path: str | Path = DEFAULT_DB) -> str:
    path = str(path)
    Path(path).unlink(missing_ok=True)
    conn = sqlite3.connect(path)
    try:
        conn.executescript(SCHEMA)
        conn.executemany("INSERT INTO storeinfo VALUES (?,?,?,?,?,?,?)", STOREINFO)
        conn.executemany(
            "INSERT INTO sales VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", SALES
        )
        conn.executemany("INSERT INTO budget VALUES (?,?,?,?,?)", BUDGET)
        conn.commit()
    finally:
        conn.close()
    return path


if __name__ == "__main__":
    p = build_database()
    print(f"seeded {p}")
