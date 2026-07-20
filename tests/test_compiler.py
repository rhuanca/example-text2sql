import unittest

from text2sql.engine.compiler import CompileError, compile, resolve_window_sql
from text2sql.engine.dialects.sqlite import SqliteDialect
from text2sql.engine.ir import SemanticQuery, TimeWindow
from tests.util import load_sales_model


class CompilerCase(unittest.TestCase):
    def setUp(self):
        self.model = load_sales_model()
        self.d = SqliteDialect()

    def c(self, ir_dict):
        return compile(SemanticQuery.from_dict(ir_dict), self.model, self.d)


class TestResolveWindow(CompilerCase):
    def test_month_window_start_equals_end_for_single_bucket(self):
        # calendar-anchored, wall-clock (no data MAX). A single-month window resolves
        # to one bucket: period_start == period_end (the previous complete month).
        sql = resolve_window_sql(TimeWindow(field="date", last=1, unit="month"), self.d)
        self.assertNotIn("MAX(", sql)
        self.assertIn("start of month", sql)                  # date_trunc('month')
        # single-bucket window: period_start and period_end are the SAME expression
        expr = "date(date('now', 'start of month'), '-1 months')"
        self.assertEqual(sql.count(expr), 2)

    def test_multi_month_window_spans_start_to_end(self):
        sql = resolve_window_sql(TimeWindow(field="date", last=6, unit="month"), self.d)
        self.assertIn("'-6 months'", sql)   # first bucket = now - 6 months
        self.assertIn("'-1 months'", sql)   # last bucket = now - 1 month

    def test_to_date_window_is_start_of_period_through_today(self):
        # calendar-to-date (YTD): from the start of the current year to today, inclusive
        sql, _ = self.c({"metrics": ["total_net_sales"], "dimensions": [],
                         "time": {"field": "date", "unit": "year", "kind": "to_date"}})
        self.assertIn("date('now', 'start of year')", sql)   # start of period
        self.assertIn("<= date('now')", sql)                 # through today
        self.assertNotIn("MAX(", sql)

    def test_resolve_to_date_spans_period_start_to_current_month(self):
        sql = resolve_window_sql(
            TimeWindow(field="date", unit="year", kind="to_date"), self.d)
        self.assertIn("date('now', 'start of year')", sql)   # period_start
        self.assertIn("date('now', 'start of month')", sql)  # period_end (current month)


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
        self.assertIn('GROUP BY "fact_sales"."product_name"', sql)
        self.assertIn('AS "total_net_sales"', sql)

    def test_filter_eq_is_parameterized(self):
        sql, params = self.c(
            {
                "metrics": ["total_net_sales"],
                "dimensions": [],
                "filters": [
                    {"field": "product_name", "op": "=", "value": "Cappuccino"}
                ],
            }
        )
        self.assertIn('"fact_sales"."product_name" = ?', sql)
        self.assertEqual(params, ["Cappuccino"])

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
        # filter on a dim_store dim forces the join
        self.assertIn("JOIN \"dim_store\"", sql)

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
                "time": {"field": "date", "last": 42, "unit": "day"},
            }
        )
        # calendar-anchored, two-sided wall-clock window (no data MAX): the last 42
        # complete days up to today -> `>= date('now') - 42 days AND < date('now')`.
        self.assertNotIn("MAX(", sql)
        self.assertIn("date(date('now'), '-42 days')", sql)
        self.assertIn('"fact_sales"."date" <', sql)
        self.assertIn('"fact_sales"."date" >=', sql)

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
        self.assertIn('FROM "fact_sales"', sql)
        self.assertIn(
            'JOIN "dim_store" ON "fact_sales"."store_id" = "dim_store"."store_id"', sql
        )
        self.assertIn('GROUP BY "dim_store"."market"', sql)

    def test_budget_vs_actual_is_aggregate_then_join(self):
        sql, _ = self.c(
            {
                "metrics": ["total_net_sales", "sales_goal"],
                "dimensions": ["store_id"],
            }
        )
        # two aggregated CTEs joined on the key
        self.assertIn("WITH", sql)
        self.assertIn("agg_fact_sales", sql)
        self.assertIn("agg_fact_budget", sql)
        self.assertIn('JOIN agg_', sql)
        # fan-out guard: the raw physical tables are never joined to each other
        self.assertNotIn('JOIN "fact_budget"', sql)
        self.assertNotIn('JOIN "fact_sales"', sql)
        # both metrics surface
        self.assertIn('AS "total_net_sales"', sql)
        self.assertIn('AS "sales_goal"', sql)

    def test_multibase_non_shared_dimension_rejected(self):
        with self.assertRaises(CompileError):
            self.c(
                {
                    "metrics": ["total_net_sales", "sales_goal"],
                    "dimensions": ["product_name"],
                }
            )


if __name__ == "__main__":
    unittest.main()
