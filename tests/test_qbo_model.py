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


class TestModel(QboCase):
    def test_model_loads(self):
        self.assertEqual(self.model.name, "qbo_finance")
        names = {m.name for m in self.model.metrics}
        self.assertEqual(
            names,
            {"total_amount", "transaction_count", "invoiced_amount", "units_invoiced"},
        )

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
    def test_total_revenue(self):
        _, rows = self.run_ir(
            {
                "metrics": ["total_amount"],
                "dimensions": [],
                "filters": [{"field": "classification", "op": "=", "value": "Revenue"}],
            }
        )
        self.assertEqual(rows, [(96000.0,)])

    def test_spend_with_vendor_matches_cogs(self):
        _, rows = self.run_ir(
            {
                "metrics": ["total_amount"],
                "dimensions": [],
                "filters": [{"field": "vendor", "op": "=", "value": "Sysco"}],
            }
        )
        self.assertEqual(rows, [(21600.0,)])

    def test_transactions_per_entity(self):
        _, rows = self.run_ir(
            {"metrics": ["transaction_count"], "dimensions": ["entity"]}
        )
        # 7 accounts x 6 months = 42 distinct transactions per entity
        self.assertEqual(dict(rows), {"Coffee US": 42, "Coffee EU": 42})


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
        self.assertEqual(by_entity["Coffee US"], (6480.0, 108000.0))
        self.assertEqual(by_entity["Coffee EU"], (3888.0, 64800.0))


if __name__ == "__main__":
    unittest.main()
