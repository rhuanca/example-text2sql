import sqlite3
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

    def raw(self, sql, params=()):
        conn = sqlite3.connect(self.db)
        try:
            return conn.execute(sql, params).fetchall()
        finally:
            conn.close()

    def run_ir(self, d):
        sql, params = compile(SemanticQuery.from_dict(d), self.model, self.dialect)
        return self.exec.run(sql, params)

    def test_weekly_cappuccino_matches_raw_aggregate(self):
        columns, rows = self.run_ir(
            {
                "metrics": ["total_net_sales", "units_sold"],
                "dimensions": ["iso_year", "iso_week"],
                "filters": [{"field": "product_name", "op": "=", "value": "Cappuccino"}],
                "order_by": [{"field": "iso_year", "dir": "asc"},
                             {"field": "iso_week", "dir": "asc"}],
            }
        )
        self.assertEqual(
            columns, ["iso_year", "iso_week", "total_net_sales", "units_sold"]
        )
        # The engine's per-week Cappuccino totals must equal a direct aggregate
        # over fact_sales applying the same deleted-line exclusion.
        expected = self.raw(
            "SELECT iso_year, iso_week, "
            "  SUM(CASE WHEN transaction_deleted=0 THEN item_net_sales ELSE 0 END), "
            "  SUM(CASE WHEN transaction_deleted=0 THEN quantity ELSE 0 END) "
            "FROM fact_sales WHERE product_name='Cappuccino' "
            "GROUP BY iso_year, iso_week ORDER BY iso_year, iso_week"
        )
        self.assertEqual(len(rows), len(expected))
        for got, exp in zip(rows, expected):
            self.assertEqual(got[0], exp[0])  # iso_year
            self.assertEqual(got[1], exp[1])  # iso_week
            self.assertAlmostEqual(got[2], exp[2], places=2)  # net sales
            self.assertEqual(got[3], exp[3])  # units

    def test_budget_vs_actual_no_fanout(self):
        """The multi-base compiler must aggregate budget in its own subquery, so
        a store's budget is never multiplied by its number of sales lines."""
        columns, rows = self.run_ir(
            {
                "metrics": ["total_net_sales", "sales_goal"],
                "dimensions": ["store_id"],
                "order_by": [{"field": "store_id", "dir": "asc"}],
            }
        )
        self.assertEqual(columns, ["store_id", "total_net_sales", "sales_goal"])
        by_store = {r[0]: r for r in rows}

        raw_budget = dict(
            self.raw("SELECT store_id, SUM(budget_net_sales) FROM fact_budget "
                     "GROUP BY store_id")
        )
        raw_sales = dict(
            self.raw("SELECT store_id, "
                     "SUM(CASE WHEN transaction_deleted=0 THEN item_net_sales ELSE 0 END) "
                     "FROM fact_sales GROUP BY store_id")
        )
        self.assertEqual(set(by_store), set(raw_budget))
        for store, (_sid, net, bud) in by_store.items():
            # Budget equals the raw per-store budget sum, NOT that times the
            # sales-line count -> the fan-out guard held.
            self.assertAlmostEqual(bud, raw_budget[store], places=2)
            self.assertAlmostEqual(net, raw_sales[store], places=2)


if __name__ == "__main__":
    unittest.main()
