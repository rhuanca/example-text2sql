import tempfile
import unittest
from pathlib import Path

from text2sql.db.seed import build_database
from text2sql.engine.compiler import compile
from text2sql.engine.dialects.sqlite import SqliteDialect
from text2sql.engine.executor import SqliteExecutor
from text2sql.engine.ir import SemanticQuery
from tests.util import load_sales_model


class TestExecutor(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.db = build_database(Path(cls.tmp.name) / "demo.db")
        cls.model = load_sales_model()
        cls.dialect = SqliteDialect()
        cls.exec = SqliteExecutor(cls.db)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_dozen_glazed_weekly(self):
        ir = SemanticQuery.from_dict(
            {
                "metrics": ["total_net_sales", "units_sold"],
                "dimensions": ["iso_year", "iso_week"],
                "filters": [
                    {"field": "product_name", "op": "=", "value": "Dozen Glazed"}
                ],
                "order_by": [{"field": "iso_week", "dir": "asc"}],
            }
        )
        sql, params = compile(ir, self.model, self.dialect)
        columns, rows = self.exec.run(sql, params)
        self.assertEqual(columns, ["iso_year", "iso_week", "total_net_sales", "units_sold"])
        self.assertEqual(len(rows), 2)
        # week 10 (all stores): T1 12.49 + T2 24.98 + T5 return -12.49 + FC5100 T9 12.49
        #   = 37.47 (deleted T4 excluded). units: 1 + 2 + 1 + 1 = 5.
        self.assertEqual(rows[0][1], 10)
        self.assertAlmostEqual(rows[0][2], 37.47, places=2)
        self.assertEqual(rows[0][3], 5)
        # week 11: T6 12.49 + T7 37.47 = 49.96
        self.assertEqual(rows[1][1], 11)
        self.assertAlmostEqual(rows[1][2], 49.96, places=2)

    def test_budget_vs_actual_no_fanout(self):
        ir = SemanticQuery.from_dict(
            {
                "metrics": ["total_net_sales", "total_budget"],
                "dimensions": ["fc_number"],
                "order_by": [{"field": "fc_number", "dir": "asc"}],
            }
        )
        sql, params = compile(ir, self.model, self.dialect)
        columns, rows = self.exec.run(sql, params)
        self.assertEqual(columns, ["fc_number", "total_net_sales", "total_budget"])
        by_store = {r[0]: r for r in rows}
        # FC5063 budget = 3500 + 3600 = 7100 (NOT fanned out across sales lines)
        self.assertAlmostEqual(by_store["FC5063"][2], 7100.00, places=2)
        self.assertAlmostEqual(by_store["FC5100"][2], 5700.00, places=2)


if __name__ == "__main__":
    unittest.main()
