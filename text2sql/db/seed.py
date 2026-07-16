"""Build a SQLite database for the product_sales model.

Deterministic synthetic data: two years of weekly, store-by-store product sales
plus a matching weekly budget. No randomness and no wall-clock reads (only
`datetime` arithmetic and `math.sin` for a smooth seasonal curve), so the same
rows are produced on every run and tests stay reproducible.

Run as a module to create ./demo.db:
    uv run python -m text2sql.db.seed
"""

from __future__ import annotations

import datetime
import math
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

# store_id, market, region, state, corporate_franchise, lifecycle_stage, open_date
# Markets match the model's advertised sample_values; lifecycle has variety so
# the "open stores only" style filters actually exclude something.
DIM_STORE = [
    ("ST001", "Houston", "South", "TX", "Corporate", "Open", "2019-03-01"),
    ("ST002", "Houston", "South", "TX", "Franchise", "Open", "2021-07-15"),
    ("ST003", "Dallas", "South", "TX", "Corporate", "Open", "2020-01-10"),
    ("ST004", "Dallas", "South", "TX", "Franchise", "Open", "2022-05-20"),
    ("ST005", "San Antonio", "South", "TX", "Corporate", "Open", "2020-09-01"),
    ("ST006", "Denver", "Mountain", "CO", "Corporate", "Open", "2018-11-05"),
    ("ST007", "Portland", "Pacific", "OR", "Franchise", "Temporarily Closed", "2021-02-01"),
    ("ST008", "Phoenix", "Mountain", "AZ", "Franchise", "Terminated", "2019-06-30"),
]

# Relative store size (bigger stores sell more). Deterministic, per store.
STORE_FACTOR = {
    "ST001": 1.3, "ST002": 0.9, "ST003": 1.1, "ST004": 0.8,
    "ST005": 1.0, "ST006": 1.2, "ST007": 0.7, "ST008": 0.6,
}

# product_name, category_name, unit_price, popularity_weight
PRODUCTS = [
    ("Cappuccino", "Espresso Drinks", 4.25, 1.0),
    ("Vanilla Latte", "Espresso Drinks", 4.75, 0.8),
    ("Americano", "Espresso Drinks", 3.50, 0.7),
    ("Black Coffee", "Coffee", 2.50, 0.6),
    ("Cold Brew", "Coffee", 3.75, 0.5),
    ("Croissant", "Pastries", 3.25, 0.5),
]

YEARS = [2025, 2026]
WEEKS = range(1, 53)
YEAR_GROWTH = {2025: 1.0, 2026: 1.10}  # 2026 is a ~10% bigger year
BASE_UNITS = 6  # baseline weekly units for a weight-1 product at a factor-1 store


def _week_date(year: int, week: int) -> str:
    """A stable Monday-ish date for (year, week): Jan 1 + (week-1)*7 days."""
    d = datetime.date(year, 1, 1) + datetime.timedelta(days=(week - 1) * 7)
    return d.isoformat()


def _generate():
    """Return (sales_rows, budget_rows) as lists of tuples matching the schema."""
    sales: list[tuple] = []
    budget: list[tuple] = []

    for store_id, _market, _region, _state, _cf, _life, _open in DIM_STORE:
        factor = STORE_FACTOR[store_id]
        for year in YEARS:
            growth = YEAR_GROWTH[year]
            for w in WEEKS:
                date = _week_date(year, w)
                # Smooth seasonal swing (+/-15%) peaking mid-year.
                seasonal = 1.0 + 0.15 * math.sin(2 * math.pi * (w - 1) / 52)
                week_total = 0.0

                for p_idx, (name, cat, price, weight) in enumerate(PRODUCTS):
                    # ~1/3 of lines are online, deterministically.
                    channel = "Online" if (p_idx + w) % 3 == 0 else "At Shop"
                    demand = BASE_UNITS * factor * weight * growth * seasonal
                    qty = max(1, round(demand))
                    net = round(price * qty, 2)
                    week_total += net
                    # Pair adjacent products into the same order so traffic
                    # (distinct transaction_id) is a plausible order count.
                    order = p_idx // 2
                    tid = f"{store_id}-{year}W{w:02d}-{channel[0]}-{order}"
                    sales.append(
                        (tid, store_id, date, w, year, name, cat, channel,
                         net, qty, 0, 0)
                    )

                # A voided line every 8th week: deleted, so metrics exclude it.
                if w % 8 == 0:
                    sales.append(
                        (f"{store_id}-{year}W{w:02d}-DEL", store_id, date, w, year,
                         "Cappuccino", "Espresso Drinks", "At Shop",
                         4.25, 1, 1, 0)
                    )
                # A refund every 10th week: negative net, flagged as a return.
                if w % 10 == 0:
                    sales.append(
                        (f"{store_id}-{year}W{w:02d}-RET", store_id, date, w, year,
                         "Vanilla Latte", "Espresso Drinks", "At Shop",
                         -4.75, 1, 0, 1)
                    )

                # One budget row per store per week, tracking near the actuals.
                budget.append(
                    (store_id, date, w, year, round(week_total * 1.03, 2))
                )

    return sales, budget


def build_database(path: str | Path = DEFAULT_DB) -> str:
    path = str(path)
    Path(path).unlink(missing_ok=True)
    sales, budget = _generate()
    conn = sqlite3.connect(path)
    try:
        conn.executescript(SCHEMA)
        conn.executemany("INSERT INTO dim_store VALUES (?,?,?,?,?,?,?)", DIM_STORE)
        conn.executemany(
            "INSERT INTO fact_sales VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", sales
        )
        conn.executemany("INSERT INTO fact_budget VALUES (?,?,?,?,?)", budget)
        conn.commit()
    finally:
        conn.close()
    return path


if __name__ == "__main__":
    p = build_database()
    sales, budget = _generate()
    print(f"seeded {p}: {len(DIM_STORE)} stores, {len(sales)} sales rows, "
          f"{len(budget)} budget rows")
