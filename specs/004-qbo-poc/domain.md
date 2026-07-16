# 004 — QuickBooks Online (QBO) semantic-model POC

Prove the text2sql engine works on a real QuickBooks Online general-ledger
export. Same invariant as the sales demo: **the LLM never emits SQL** — it only
picks metrics/dimensions/filters from `models/qbo.yml`, and the deterministic
compiler produces the SQL.

## Source tables (13 given), and how we use them

The export is SQL Server (`dbo`, `varchar(max)`), one table per QBO object plus
consolidated / hierarchy tables. For this POC we model **5** of them:

| Logical | Physical | Role | Grain |
|---|---|---|---|
| `txn` | `qbo_txn_consolidated` | **primary fact** | one GL posting line |
| `invoices` | `qbo_invoices` | **second fact** (fan-out demo) | one invoice line |
| `accounts` | `qbo_accounts` | dimension (chart of accounts) | one account |
| `acct_hier` | `hierarchy_by_account` | dimension (account rollup) | one account number |
| `class_hier` | `hierarchy_by_class` | dimension (class → department) | one class |

The remaining source tables are **out of scope** for the POC:
`qbo_bills`, `qbo_purchase`, `qbo_vendor_credit`, `qbo_deposit`, `qbo_journals`
(per-object source facts that already roll up into `qbo_txn_consolidated`), and
`qbo_txn_detail_by_account_raw`, `qbo_txn_with_splits_raw` (raw pre-consolidation
variants; the splits table is the only one carrying explicit `Debit`/`Credit`).

## The main table: `qbo_txn_consolidated`

Essentially a **Transaction Detail by Account** report, flattened across every
transaction type and every entity. One row = one line of a transaction posting
to one account. `Amount` is a **single signed value stored as text** — the
compiler casts it (`CAST(Amount AS REAL)`). This is why the earlier "signed vs
debit/credit" question resolved to *signed amount*: debit/credit only survive in
the raw/journal tables, not here.

## Join keys (best-guess from column names — no FK constraints in the DDL)

| From | To | On |
|---|---|---|
| `qbo_txn_consolidated.AccountID` **+ `Entity`** | `qbo_accounts.Id` **+ `Entity`** | account master (**composite**) |
| `qbo_txn_consolidated.Account_Number` | `hierarchy_by_account.AcctNum` | P&L rollup (Lvl1–3) |
| `qbo_txn_consolidated.Class` | `hierarchy_by_class.Class` | department rollup |

These are encoded in `relationships:` in `models/qbo.yml` and verified against
real data later. The seeder generates data that honors them.

**Composite account join.** An account `Id` is only unique *within* a company,
so `txn → qbo_accounts` matches on `AccountID` **and** `Entity` — the upstream
ETL joins on `AccountID+Entity` for the same reason. The engine supports this
via multi-column relationships (`also:` in the YAML), which compile to an
`AND`-ed `ON` clause. The seeder deliberately reuses account `Id`s 1–7 across
both companies (14 rows), so a single-column join would match each txn line to
both companies' accounts and **double** the total; the composite key keeps it
1:1 (the test asserts the naive join is exactly 2× the composite one). The
account-hierarchy and class rollups have no `Entity` column (a shared chart of
accounts), so those joins stay single-column.

**Data volume.** The seeder generates weekly rows across **two full years
(2025–2026)** per entity × account × class — ~4.4k `qbo_txn_consolidated` lines
plus ~600 `qbo_invoices` lines (~5k records) — with ~12% YoY growth and a mild
seasonal wave, so week-over-week and year-over-year cuts show real movement.

## Sign / amount convention (POC choice)

Real QBO GL amounts are signed so a balanced entry nets to zero, which makes a
bare `SUM(Amount)` meaningless as a headline number. For an intuitive demo we
seed **positive magnitudes** and let the `classification` dimension carry the
Revenue-vs-Expense meaning:

- Revenue lines: positive `Amount`.
- Expense lines: positive `Amount` (magnitude).
- "Revenue" / "expenses" are expressed as `total_amount` **+ a filter on
  `classification`**, not as separate metrics. This keeps the model faithful to
  the real columns (classification lives on `qbo_accounts`, reached by join) and
  avoids fragile conditional aggregation over a joined column.

## Derived time columns

`qbo_txn_consolidated` / `qbo_invoices` only carry a text `Date`. To group by
month/quarter/year the seeder adds `Year`, `Month`, `Quarter` helper columns —
exactly as the sales model precomputes `iso_week` / `iso_year`. In production
these come from the ETL/view, not the raw QBO object.

## Metrics & dimensions (see `models/qbo.yml`)

- **Metrics:** `total_amount`, `transaction_count` (on `txn`);
  `invoiced_amount`, `units_invoiced` (on `invoices`).
- **Dimensions:** account name, classification, account type/subtype, account
  hierarchy Lvl1–3, department / department category, entity, transaction type,
  customer, vendor, date/year/month/quarter; plus item / invoice class on
  invoices.

## Fan-out guard demo

"Invoiced amount vs posted amount by entity" pulls a metric from **both** facts
grouped by `entity` → the compiler's multi-base path aggregates each fact in its
own subquery, then joins on the shared `Entity` key. An invoice with many lines
never fans out a `txn` posting line. `Entity` is declared on both facts so the
group-by key is shared (mirrors `budget_store_id` in the sales model).

## Success criteria

The ~10 questions in `eval/cases_qbo.yml` compile and run against `demo_qbo.db`
returning sensible results, and the fan-out question compiles to the multi-base
(`WITH ... agg_*`) shape. Covered by `tests/test_qbo_model.py`.
