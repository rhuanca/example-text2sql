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
CREATE TABLE dim_store (
    store_id           TEXT PRIMARY KEY,
    market              TEXT,
    region              TEXT,
    state               TEXT,
    corporate_franchise TEXT,
    lifecycle_stage     TEXT,
    open_date           TEXT
);

CREATE TABLE fact_sales (
    transaction_id      TEXT,
    store_id           TEXT,
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

CREATE TABLE fact_budget (
    store_id        TEXT,
    date             TEXT,
    iso_week         INTEGER,
    iso_year         INTEGER,
    budget_net_sales REAL
);
"""

DIM_STORE = [
    ("ST001", "Denver", "Mountain", "CO", "Corporate", "Open", "2020-01-15"),
    ("ST002", "Portland", "Pacific", "OR", "Franchise", "Open", "2022-06-01"),
]

# (txn, fc, date, week, year, product, category, location, net, qty, deleted, return)
FACT_SALES = [
    # ---- ST001, ISO week 10 (2026-03-02) ----
    ("T1", "ST001", "2026-03-02", 10, 2026, "Cappuccino", "Espresso Drinks", "At Shop", 12.49, 1, 0, 0),
    ("T1", "ST001", "2026-03-02", 10, 2026, "Croissant", "Pastries", "At Shop", 2.50, 1, 0, 0),
    ("T2", "ST001", "2026-03-02", 10, 2026, "Cappuccino", "Espresso Drinks", "Online", 24.98, 2, 0, 0),
    ("T3", "ST001", "2026-03-02", 10, 2026, "Vanilla Latte", "Espresso Drinks", "At Shop", 9.00, 3, 0, 0),
    ("T4", "ST001", "2026-03-02", 10, 2026, "Cappuccino", "Espresso Drinks", "At Shop", 12.49, 1, 1, 0),  # deleted
    ("T5", "ST001", "2026-03-02", 10, 2026, "Cappuccino", "Espresso Drinks", "At Shop", -12.49, 1, 0, 1),  # return
    # ---- ST001, ISO week 11 (2026-03-09) ----
    ("T6", "ST001", "2026-03-09", 11, 2026, "Cappuccino", "Espresso Drinks", "At Shop", 12.49, 1, 0, 0),
    ("T7", "ST001", "2026-03-09", 11, 2026, "Cappuccino", "Espresso Drinks", "Online", 37.47, 3, 0, 0),
    ("T8", "ST001", "2026-03-09", 11, 2026, "Black Coffee", "Coffee", "At Shop", 5.00, 2, 0, 0),
    # ---- ST002 ----
    ("T9", "ST002", "2026-03-02", 10, 2026, "Cappuccino", "Espresso Drinks", "At Shop", 12.49, 1, 0, 0),
    ("T10", "ST002", "2026-03-09", 11, 2026, "Vanilla Latte", "Espresso Drinks", "At Shop", 6.00, 2, 0, 0),
]

FACT_BUDGET = [
    ("ST001", "2026-03-02", 10, 2026, 3500.00),
    ("ST001", "2026-03-09", 11, 2026, 3600.00),
    ("ST002", "2026-03-02", 10, 2026, 2800.00),
    ("ST002", "2026-03-09", 11, 2026, 2900.00),
]


def build_database(path: str | Path = DEFAULT_DB) -> str:
    path = str(path)
    Path(path).unlink(missing_ok=True)
    conn = sqlite3.connect(path)
    try:
        conn.executescript(SCHEMA)
        conn.executemany("INSERT INTO dim_store VALUES (?,?,?,?,?,?,?)", DIM_STORE)
        conn.executemany(
            "INSERT INTO fact_sales VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", FACT_SALES
        )
        conn.executemany("INSERT INTO fact_budget VALUES (?,?,?,?,?)", FACT_BUDGET)
        conn.commit()
    finally:
        conn.close()
    return path


if __name__ == "__main__":
    p = build_database()
    print(f"seeded {p}")
