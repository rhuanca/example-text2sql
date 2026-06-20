import unittest

from text2sql.engine.compiler import CompileError, compile
from text2sql.engine.dialects.sqlite import SqliteDialect
from text2sql.engine.ir import SemanticQuery
from tests.util import load_sales_model


class CompilerCase(unittest.TestCase):
    def setUp(self):
        self.model = load_sales_model()
        self.d = SqliteDialect()

    def c(self, ir_dict):
        return compile(SemanticQuery.from_dict(ir_dict), self.model, self.d)


class TestSingleTable(CompilerCase):
    def test_scalar_aggregate(self):
        sql, params = self.c({"metrics": ["total_net_sales"], "dimensions": []})
        self.assertIn("SUM(CASE WHEN transaction_deleted", sql)
        self.assertNotIn("GROUP BY", sql)
        self.assertEqual(params, [])

    def test_group_by_dimension(self):
        sql, _ = self.c(
            {"metrics": ["total_net_sales"], "dimensions": ["product_name"]}
        )
        self.assertIn('GROUP BY "sales"."product_name"', sql)
        self.assertIn('AS "total_net_sales"', sql)

    def test_filter_eq_is_parameterized(self):
        sql, params = self.c(
            {
                "metrics": ["total_net_sales"],
                "dimensions": [],
                "filters": [
                    {"field": "product_name", "op": "=", "value": "Dozen Glazed"}
                ],
            }
        )
        self.assertIn('"sales"."product_name" = ?', sql)
        self.assertEqual(params, ["Dozen Glazed"])

    def test_filter_in(self):
        sql, params = self.c(
            {
                "metrics": ["total_net_sales"],
                "dimensions": [],
                "filters": [
                    {"field": "market", "op": "in", "value": ["Houston", "Dallas"]}
                ],
            }
        )
        self.assertIn("IN (?, ?)", sql)
        self.assertEqual(params, ["Houston", "Dallas"])
        # filter on a storeinfo dim forces the join
        self.assertIn("JOIN \"storeinfo\"", sql)

    def test_filter_like(self):
        sql, params = self.c(
            {
                "metrics": ["units_sold"],
                "dimensions": [],
                "filters": [{"field": "product_name", "op": "like", "value": "Dozen%"}],
            }
        )
        self.assertIn("LIKE ?", sql)
        self.assertEqual(params, ["Dozen%"])

    def test_quote_in_value_does_not_inject(self):
        sql, params = self.c(
            {
                "metrics": ["total_net_sales"],
                "dimensions": [],
                "filters": [{"field": "product_name", "op": "=", "value": "O'Brien"}],
            }
        )
        self.assertNotIn("O'Brien", sql)
        self.assertEqual(params, ["O'Brien"])

    def test_time_sugar(self):
        sql, _ = self.c(
            {
                "metrics": ["total_net_sales"],
                "dimensions": [],
                "time": {"field": "date", "last_n_days": 42},
            }
        )
        self.assertIn("date('now', '-42 days')", sql)
        self.assertIn('"sales"."date" >=', sql)

    def test_order_and_limit(self):
        sql, _ = self.c(
            {
                "metrics": ["total_net_sales"],
                "dimensions": ["iso_week"],
                "order_by": [{"field": "iso_week", "dir": "desc"}],
                "limit": 10,
            }
        )
        self.assertIn('ORDER BY "iso_week" DESC', sql)
        self.assertIn("LIMIT 10", sql)

    def test_empty_query_rejected(self):
        with self.assertRaises(CompileError):
            self.c({"metrics": [], "dimensions": []})


class TestJoinsAndFanout(CompilerCase):
    def test_cross_table_join_via_relationship(self):
        sql, _ = self.c({"metrics": ["total_net_sales"], "dimensions": ["market"]})
        self.assertIn('FROM "sales"', sql)
        self.assertIn(
            'JOIN "storeinfo" ON "sales"."fc_number" = "storeinfo"."fc_number"', sql
        )
        self.assertIn('GROUP BY "storeinfo"."market"', sql)

    def test_budget_vs_actual_is_aggregate_then_join(self):
        sql, _ = self.c(
            {
                "metrics": ["total_net_sales", "total_budget"],
                "dimensions": ["fc_number"],
            }
        )
        # two aggregated CTEs joined on the key
        self.assertIn("WITH", sql)
        self.assertIn("agg_sales", sql)
        self.assertIn("agg_budget", sql)
        self.assertIn('JOIN agg_', sql)
        # fan-out guard: the raw physical tables are never joined to each other
        self.assertNotIn('JOIN "budget"', sql)
        self.assertNotIn('JOIN "sales"', sql)
        # both metrics surface
        self.assertIn('AS "total_net_sales"', sql)
        self.assertIn('AS "total_budget"', sql)

    def test_multibase_non_shared_dimension_rejected(self):
        with self.assertRaises(CompileError):
            self.c(
                {
                    "metrics": ["total_net_sales", "total_budget"],
                    "dimensions": ["product_name"],
                }
            )


if __name__ == "__main__":
    unittest.main()
