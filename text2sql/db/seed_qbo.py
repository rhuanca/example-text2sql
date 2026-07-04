"""Build a SQLite database with small, referentially-consistent sample data for
the qbo_finance model (models/qbo.yml). Deterministic (no randomness, no clock)
so tests are reproducible.

Mirrors the real QuickBooks Online export schema for the 5 modelled tables, plus
ETL-derived Year/Month/Quarter helper columns (see specs/004-qbo-poc/domain.md).

Run as a module to create ./demo_qbo.db:
    uv run python -m text2sql.db.seed_qbo
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

DEFAULT_DB = "demo_qbo.db"

SCHEMA = """
CREATE TABLE qbo_accounts (
    Id             TEXT,
    Name           TEXT,
    Classification TEXT,
    AccountType    TEXT,
    AccountSubType TEXT,
    AcctNum        TEXT,
    CurrentBalance REAL,
    Entity         TEXT,
    PRIMARY KEY (Id, Entity)   -- an account Id is only unique within a company
);

CREATE TABLE hierarchy_by_account (
    AcctNum TEXT,
    AcctNm  TEXT,
    Acct    TEXT,
    Lvl1    TEXT,
    Lvl2    TEXT,
    Lvl3    TEXT
);

CREATE TABLE hierarchy_by_class (
    SourceClass        TEXT,
    Class              TEXT,
    Department         TEXT,
    DepartmentCategory TEXT
);

CREATE TABLE qbo_txn_consolidated (
    Date              TEXT,
    TransactionTypeID TEXT,
    position          REAL,
    TransactionType   TEXT,
    Num               TEXT,
    AccountID         TEXT,
    Account_Number    TEXT,
    Account           TEXT,
    Vendor            TEXT,
    Customer          TEXT,
    Name              TEXT,
    Class             TEXT,
    Memo_Description  TEXT,
    Amount            TEXT,   -- signed value stored as text, as in the real export
    Entity            TEXT,
    Year              INTEGER,
    Month             INTEGER,
    Quarter           INTEGER
);

CREATE TABLE qbo_invoices (
    Id                                 TEXT,
    LineNum                            REAL,
    Amount                             REAL,
    SalesItemLineDetail_ItemRef_name   TEXT,
    SalesItemLineDetail_ClassRef_name  TEXT,
    SalesItemLineDetail_Qty            REAL,
    SalesItemLineDetail_UnitPrice      REAL,
    Date                               TEXT,
    Total_Amount                       REAL,
    TransactionTypeID                  TEXT,
    Num                                TEXT,
    Entity                             TEXT,
    Year                               INTEGER,
    Month                              INTEGER
);
"""

# --- reference dimensions ---------------------------------------------------

# (Id, AcctNum, Name, Classification, AccountType, AccountSubType, base_amount)
ACCOUNTS = [
    ("1", "4000", "Product Sales", "Revenue", "Income", "SalesOfProductIncome", 5000.0),
    ("2", "4100", "Service Revenue", "Revenue", "Income", "ServiceFeeIncome", 3000.0),
    ("3", "5000", "Cost of Goods Sold", "Expense", "Cost of Goods Sold", "SuppliesMaterialsCogs", 1800.0),
    ("4", "6000", "Rent Expense", "Expense", "Expense", "Rent", 1200.0),
    ("5", "6100", "Payroll Expense", "Expense", "Expense", "PayrollExpenses", 2200.0),
    ("6", "6200", "Marketing Expense", "Expense", "Expense", "Advertising", 800.0),
    ("7", "6300", "Office Supplies", "Expense", "Expense", "OfficeGeneralAdministrativeExpenses", 400.0),
]

# AcctNum -> (AcctNm, Lvl1, Lvl2, Lvl3)
ACCT_HIER = {
    "4000": ("Product Sales", "Income", "Operating Revenue", "Product"),
    "4100": ("Service Revenue", "Income", "Operating Revenue", "Service"),
    "5000": ("Cost of Goods Sold", "Cost of Sales", "COGS", "Materials"),
    "6000": ("Rent Expense", "Operating Expenses", "Facilities", "Rent"),
    "6100": ("Payroll Expense", "Operating Expenses", "People", "Payroll"),
    "6200": ("Marketing Expense", "Operating Expenses", "Growth", "Marketing"),
    "6300": ("Office Supplies", "Operating Expenses", "G&A", "Supplies"),
}

# (Class, Department, DepartmentCategory)
CLASSES = [
    ("Retail", "Store Operations", "Operations"),
    ("Wholesale", "Wholesale", "Sales"),
    ("Online", "E-Commerce", "Sales"),
]

ENTITIES = [("Northwind Inc.", 1.0), ("Contoso SAS", 0.6)]
MONTHS = [1, 2, 3, 4, 5, 6]

CUSTOMERS = ["Acme Corp", "Globex", "Initech"]
VENDOR_BY_ACCTNUM = {
    "5000": "Sysco",
    "6000": "WeWork",
    "6100": "ADP",
    "6200": "Google Ads",
    "6300": "Staples",
}
TXN_TYPE = {  # by classification / account
    "Revenue": ("Invoice", "IN"),
    "5000": ("Bill", "BL"),  # COGS
    "Expense": ("Expense", "EX"),
}

# (item name, unit price)
ITEMS = [("Espresso Beans", 24.0), ("Cold Brew Kit", 40.0), ("Mug", 12.0)]


def _build_rows():
    accounts_rows, txn_rows, invoice_rows = [], [], []

    # Each company has its own chart of accounts; Ids collide across companies,
    # so the same Id means a different account per Entity. This is exactly why
    # the txn -> accounts join must be composite (AccountID + Entity).
    for entity, _ in ENTITIES:
        for aid, acctnum, name, classn, atype, asub, base in ACCOUNTS:
            accounts_rows.append((aid, name, classn, atype, asub, acctnum, base, entity))

    hier_acct_rows = [
        (acctnum, acctnm, f"{acctnum} {acctnm}", l1, l2, l3)
        for acctnum, (acctnm, l1, l2, l3) in ACCT_HIER.items()
    ]
    hier_class_rows = [(c, c, dept, cat) for c, dept, cat in CLASSES]

    n = 0
    pos = 0.0
    for entity, factor in ENTITIES:
        for m in MONTHS:
            quarter = (m - 1) // 3 + 1
            date = f"2026-{m:02d}-15"
            for idx, (aid, acctnum, name, classn, atype, asub, base) in enumerate(ACCOUNTS):
                n += 1
                pos += 1.0
                amount = round(base * factor * (1.0 + 0.10 * (m - 1)), 2)
                ttype, ttid = TXN_TYPE.get(acctnum, TXN_TYPE.get(classn, ("Journal", "JE")))
                cls = CLASSES[(idx + m) % len(CLASSES)][0]
                if classn == "Revenue":
                    customer = CUSTOMERS[(idx + m) % len(CUSTOMERS)]
                    vendor = ""
                    party = customer
                else:
                    vendor = VENDOR_BY_ACCTNUM.get(acctnum, "General Vendor")
                    customer = ""
                    party = vendor
                txn_rows.append((
                    date, ttid, pos, ttype, f"T{n:04d}", aid, acctnum, name,
                    vendor, customer, party, cls, f"{name} - {entity}",
                    str(amount), entity, 2026, m, quarter,
                ))

    inv_n = 0
    for entity, factor in ENTITIES:
        for m in MONTHS:
            inv_n += 1
            inv_id = f"INV{inv_n:04d}"
            date = f"2026-{m:02d}-20"
            lines = []
            for li, (item, price) in enumerate(ITEMS):
                qty = round((10 + 5 * li) * factor)
                lines.append((li + 1, item, qty, price, round(qty * price, 2)))
            total = round(sum(l[4] for l in lines), 2)
            for linenum, item, qty, price, line_amt in lines:
                cls = CLASSES[(linenum + m) % len(CLASSES)][0]
                invoice_rows.append((
                    inv_id, float(linenum), line_amt, item, cls, float(qty), price,
                    date, total, "IN", f"I{inv_n:04d}", entity, 2026, m,
                ))

    return accounts_rows, hier_acct_rows, hier_class_rows, txn_rows, invoice_rows


def build_database(path: str | Path = DEFAULT_DB) -> str:
    path = str(path)
    Path(path).unlink(missing_ok=True)
    accounts, hier_acct, hier_class, txns, invoices = _build_rows()
    conn = sqlite3.connect(path)
    try:
        conn.executescript(SCHEMA)
        conn.executemany("INSERT INTO qbo_accounts VALUES (?,?,?,?,?,?,?,?)", accounts)
        conn.executemany("INSERT INTO hierarchy_by_account VALUES (?,?,?,?,?,?)", hier_acct)
        conn.executemany("INSERT INTO hierarchy_by_class VALUES (?,?,?,?)", hier_class)
        conn.executemany(
            "INSERT INTO qbo_txn_consolidated VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            txns,
        )
        conn.executemany(
            "INSERT INTO qbo_invoices VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)", invoices
        )
        conn.commit()
    finally:
        conn.close()
    return path


if __name__ == "__main__":
    p = build_database()
    print(f"seeded {p}")
