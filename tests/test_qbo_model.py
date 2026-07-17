"""Tests for the QBO finance POC: the semantic model loads, every eval case
compiles against it, representative queries run against the synthetic DB with the
expected numbers, and the fan-out question uses the multi-base compiler path.
"""

import sqlite3
import tempfile
import unittest
from pathlib import Path

from text2sql.db.seed_qbo import build_database
from text2sql.engine.compiler import compile
from text2sql.engine.dialects.sqlite import SqliteDialect
from text2sql.engine.ir import SemanticQuery
from text2sql.eval.dataset import load_cases
from text2sql.semantic.model import load_model

REPO_ROOT = Path(__file__).resolve().parents[1]
MODEL_PATH = REPO_ROOT / "models" / "qbo.yml"
CASES_PATH = REPO_ROOT / "eval" / "cases_qbo.yml"


class QboCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.db = build_database(Path(cls.tmp.name) / "demo_qbo.db")
        cls.model = load_model(MODEL_PATH)
        cls.dialect = SqliteDialect()

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def run_ir(self, ir_dict):
        sql, params = compile(
            SemanticQuery.from_dict(ir_dict), self.model, self.dialect
        )
        conn = sqlite3.connect(self.db)
        try:
            return sql, conn.execute(sql, params).fetchall()
        finally:
            conn.close()

    def raw(self, sql, params=()):
        """Query the seeded tables directly, for data-derived expectations."""
        conn = sqlite3.connect(self.db)
        try:
            return conn.execute(sql, params).fetchall()
        finally:
            conn.close()


class TestModel(QboCase):
    def test_model_loads(self):
        self.assertEqual(self.model.name, "qbo_finance")
        names = {m.name for m in self.model.metrics}
        self.assertEqual(
            names,
            {"total_amount", "net_income", "transaction_count",
             "invoiced_amount", "units_invoiced"},
        )

    def test_classification_is_non_additive(self):
        self.assertFalse(self.model.dimension("classification").additive)
        self.assertTrue(self.model.dimension("department").additive)  # default
        self.assertEqual(self.model.metric("net_income").joins, ["accounts"])

    def test_descriptions_and_time_types_load(self):
        self.assertTrue(self.model.metric("net_income").description)
        self.assertEqual(self.model.dimension("txn_month").type, "month")
        self.assertEqual(self.model.dimension("txn_year").type, "year")

    def test_net_income_joins_accounts_and_nets_revenue_minus_expense(self):
        sql, rows = self.run_ir({
            "metrics": ["net_income"], "dimensions": ["txn_month"],
            "filters": [{"field": "txn_year", "op": "=", "value": 2026}],
        })
        self.assertIn("qbo_accounts", sql)  # the metric's declared join is added
        net_by_month = {r[0]: r[1] for r in rows}
        conn = sqlite3.connect(self.db)

        def raw(klass):
            return conn.execute(
                "SELECT SUM(CAST(t.Amount AS REAL)) FROM qbo_txn_consolidated t "
                "JOIN qbo_accounts a ON t.AccountID=a.Id AND t.Entity=a.Entity "
                "WHERE t.Year=2026 AND t.Month=1 AND a.Classification=?", (klass,),
            ).fetchone()[0]

        self.assertAlmostEqual(net_by_month[1], raw("Revenue") - raw("Expense"), places=2)
        conn.close()

    def test_two_fact_tables_present(self):
        metric_tables = {m.table for m in self.model.metrics}
        self.assertIn("txn", metric_tables)
        self.assertIn("invoices", metric_tables)


class TestEvalCasesCompile(QboCase):
    def test_every_case_field_is_defined_and_compiles(self):
        cases = load_cases(CASES_PATH)
        self.assertGreaterEqual(len(cases), 10)
        for c in cases:
            with self.subTest(case=c.id):
                q = c.expected
                for name in list(q.metrics) + list(q.dimensions):
                    self.assertTrue(
                        self.model.has_field(name), f"{c.id}: unknown field {name!r}"
                    )
                for f in q.filters:
                    self.assertTrue(
                        self.model.has_field(f.field),
                        f"{c.id}: unknown filter field {f.field!r}",
                    )
                # every case must compile and execute
                sql, rows = self.run_ir(q.to_dict())
                self.assertTrue(sql.lower().lstrip().startswith(("select", "with")))


class TestResults(QboCase):
    def test_total_revenue_matches_raw(self):
        _, rows = self.run_ir(
            {
                "metrics": ["total_amount"],
                "dimensions": [],
                "filters": [{"field": "classification", "op": "=", "value": "Revenue"}],
            }
        )
        expected = self.raw(
            "SELECT SUM(CAST(t.Amount AS REAL)) FROM qbo_txn_consolidated t "
            "JOIN qbo_accounts a ON t.AccountID = a.Id AND t.Entity = a.Entity "
            "WHERE a.Classification = 'Revenue'"
        )[0][0]
        self.assertAlmostEqual(rows[0][0], expected, places=2)

    def test_spend_with_vendor_matches_raw(self):
        _, rows = self.run_ir(
            {
                "metrics": ["total_amount"],
                "dimensions": [],
                "filters": [{"field": "vendor", "op": "=", "value": "Sysco"}],
            }
        )
        expected = self.raw(
            "SELECT SUM(CAST(Amount AS REAL)) FROM qbo_txn_consolidated WHERE Vendor = 'Sysco'"
        )[0][0]
        self.assertAlmostEqual(rows[0][0], expected, places=2)

    def test_transactions_per_entity_matches_raw(self):
        _, rows = self.run_ir(
            {"metrics": ["transaction_count"], "dimensions": ["entity"]}
        )
        got = dict(rows)
        expected = dict(
            self.raw(
                "SELECT Entity, COUNT(DISTINCT Num) FROM qbo_txn_consolidated GROUP BY Entity"
            )
        )
        self.assertEqual(got, expected)
        # symmetric generation -> both companies have the same count
        self.assertEqual(len(got), 2)
        self.assertEqual(len(set(got.values())), 1)


class TestData(QboCase):
    def test_spans_two_years(self):
        years = [r[0] for r in self.raw(
            "SELECT DISTINCT Year FROM qbo_txn_consolidated ORDER BY Year"
        )]
        self.assertEqual(years, [2025, 2026])

    def test_weekly_grain_present(self):
        lo, hi = self.raw("SELECT MIN(Week), MAX(Week) FROM qbo_txn_consolidated")[0]
        self.assertEqual((lo, hi), (1, 52))

    def test_volume_is_a_few_thousand_rows(self):
        n = self.raw("SELECT COUNT(*) FROM qbo_txn_consolidated")[0][0]
        self.assertGreaterEqual(n, 4000)

    def test_revenue_grows_year_over_year(self):
        rows = self.raw(
            "SELECT t.Year, SUM(CAST(t.Amount AS REAL)) FROM qbo_txn_consolidated t "
            "JOIN qbo_accounts a ON t.AccountID = a.Id AND t.Entity = a.Entity "
            "WHERE a.Classification = 'Revenue' GROUP BY t.Year ORDER BY t.Year"
        )
        by_year = dict(rows)
        self.assertGreater(by_year[2026], by_year[2025])


class TestCompositeJoin(QboCase):
    def test_join_ands_both_key_columns(self):
        sql, _ = self.run_ir(
            {
                "metrics": ["total_amount"],
                "dimensions": ["classification"],
            }
        )
        self.assertIn(
            '"qbo_txn_consolidated"."AccountID" = "qbo_accounts"."Id"', sql
        )
        self.assertIn(
            '"qbo_txn_consolidated"."Entity" = "qbo_accounts"."Entity"', sql
        )
        self.assertIn(" AND ", sql)

    def test_composite_join_does_not_double_count(self):
        # Account Ids are reused across both companies, so joining on AccountID
        # alone matches each txn line to BOTH entities' accounts and doubles the
        # total. The composite key (AccountID + Entity) keeps it 1:1.
        _, rows = self.run_ir(
            {
                "metrics": ["total_amount"],
                "dimensions": [],
                "filters": [{"field": "classification", "op": "=", "value": "Revenue"}],
            }
        )
        composite = rows[0][0]
        naive = self.raw(
            "SELECT SUM(CAST(t.Amount AS REAL)) FROM qbo_txn_consolidated t "
            "JOIN qbo_accounts a ON t.AccountID = a.Id "  # no Entity -> fan-out
            "WHERE a.Classification = 'Revenue'"
        )[0][0]
        # two entities share the ids, so the naive join doubles the composite total
        self.assertAlmostEqual(naive, composite * 2, places=2)


class TestFanOutGuard(QboCase):
    def test_multibase_shape_and_values(self):
        sql, rows = self.run_ir(
            {
                "metrics": ["invoiced_amount", "total_amount"],
                "dimensions": ["entity"],
            }
        )
        # two metrics from two fact tables -> multi-base (aggregate-then-join)
        self.assertIn("WITH", sql)
        self.assertIn("agg_txn", sql)
        self.assertIn("agg_invoices", sql)
        by_entity = {r[0]: (r[1], r[2]) for r in rows}
        # each fact is aggregated in its own subquery (no fan-out), so the
        # per-entity totals match the raw per-entity sums of each table
        for entity, (invoiced, posted) in by_entity.items():
            exp_inv = self.raw(
                "SELECT SUM(CAST(Amount AS REAL)) FROM qbo_invoices WHERE Entity = ?",
                (entity,),
            )[0][0]
            exp_txn = self.raw(
                "SELECT SUM(CAST(Amount AS REAL)) FROM qbo_txn_consolidated WHERE Entity = ?",
                (entity,),
            )[0][0]
            self.assertAlmostEqual(invoiced, exp_inv, places=2)
            self.assertAlmostEqual(posted, exp_txn, places=2)


class TestVerifiedQueries(QboCase):
    def test_model_verified_queries_are_valid_semantic_sql(self):
        # Every verified query in the model must parse (semantic SQL), compile, and
        # run — so the few-shot examples we feed the planner stay correct.
        from text2sql.engine.semantic_sql import compile_semantic_sql

        self.assertTrue(self.model.verified_queries)  # the model declares some
        conn = sqlite3.connect(self.db)
        try:
            for vq in self.model.verified_queries:
                sql, params, _ = compile_semantic_sql(vq.sql, self.model, self.dialect)
                cur = conn.execute(sql, params)
                self.assertTrue([d[0] for d in cur.description], vq.question)
        finally:
            conn.close()


if __name__ == "__main__":
    unittest.main()
