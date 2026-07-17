"""Semantic SQL front-end: parse + validate LLM SQL into the IR, the validation
safety boundary, and the data-anchored relative window (the reported bug)."""

import sqlite3
import tempfile
import unittest
from pathlib import Path

from text2sql.db.seed import build_database
from text2sql.engine.compare import Comparison
from text2sql.engine.compiler import compile
from text2sql.engine.dialects.sqlite import SqliteDialect
from text2sql.engine.engine import Engine
from text2sql.engine.executor import SqliteExecutor
from text2sql.engine.semantic_sql import (
    QueryShape,
    SemanticSqlError,
    compile_semantic_sql,
    parse_to_ir,
    to_plan,
)
from tests.util import load_sales_model

_WINDOW_SQL = (
    "SELECT iso_week, total_net_sales, "
    "100.0 * (total_net_sales - LAG(total_net_sales) OVER (ORDER BY iso_week)) "
    "/ LAG(total_net_sales) OVER (ORDER BY iso_week) AS pct_change "
    "FROM product_sales WHERE product_name = 'Cappuccino' "
    "GROUP BY iso_week ORDER BY iso_week"
)

_PIVOT_SQL = (
    "SELECT iso_week, "
    "SUM(CASE WHEN iso_year = 2025 THEN total_net_sales END) AS ns_2025, "
    "SUM(CASE WHEN iso_year = 2026 THEN total_net_sales END) AS ns_2026 "
    "FROM product_sales WHERE product_name = 'Cappuccino' GROUP BY iso_week"
)


class _SqlPlanner:
    """Stub planner that returns a fixed semantic SQL string."""

    def __init__(self, sql):
        self.sql = sql

    def plan(self, question, model, error=None, history=None):
        return self.sql


class SqlCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.model = load_sales_model()
        cls.dialect = SqliteDialect()

    def ir(self, sql):
        return parse_to_ir(sql, self.model)


class TestNormalize(SqlCase):
    def test_select_classifies_metrics_and_dimensions(self):
        ir = self.ir(
            "SELECT iso_week, total_net_sales FROM product_sales "
            "WHERE product_name = 'Cappuccino' GROUP BY iso_week ORDER BY iso_week"
        )
        self.assertEqual(ir.metrics, ["total_net_sales"])
        self.assertEqual(ir.dimensions, ["iso_week"])
        self.assertEqual(ir.filters[0].field, "product_name")
        self.assertEqual(ir.filters[0].value, "Cappuccino")
        self.assertEqual(ir.order_by[0].field, "iso_week")

    def test_in_like_and_having_and_limit(self):
        ir = self.ir(
            "SELECT market, total_net_sales FROM product_sales "
            "WHERE market IN ('Houston', 'Dallas') AND state LIKE 'T%' "
            "GROUP BY market HAVING total_net_sales > 1000 "
            "ORDER BY total_net_sales DESC LIMIT 3"
        )
        ops = {(f.field, f.op) for f in ir.filters}
        self.assertIn(("market", "in"), ops)
        self.assertIn(("state", "like"), ops)
        self.assertEqual(ir.having[0].field, "total_net_sales")
        self.assertEqual(ir.having[0].op, ">")
        self.assertEqual(ir.limit, 3)

    def test_last_period_becomes_data_anchored_window(self):
        ir = self.ir(
            "SELECT iso_week, total_net_sales FROM product_sales "
            "WHERE date >= last_period(6, 'week') GROUP BY iso_week"
        )
        self.assertEqual((ir.time.field, ir.time.last, ir.time.unit, ir.time.anchor),
                         ("date", 6, "week", "data"))


class TestValidationRejects(SqlCase):
    def _reject(self, sql):
        with self.assertRaises(SemanticSqlError):
            self.ir(sql)

    def test_unknown_column(self):
        self._reject("SELECT nope FROM product_sales")

    def test_physical_column(self):
        self._reject("SELECT item_net_sales FROM product_sales")

    def test_physical_table(self):
        self._reject("SELECT total_net_sales FROM fact_sales")

    def test_join(self):
        self._reject(
            "SELECT total_net_sales FROM product_sales a JOIN dim_store b ON a.x=b.x"
        )

    def test_star(self):
        self._reject("SELECT * FROM product_sales")

    def test_non_select(self):
        self._reject("DELETE FROM product_sales")

    def test_subquery(self):
        self._reject(
            "SELECT total_net_sales FROM product_sales "
            "WHERE market IN (SELECT market FROM product_sales)"
        )

    def test_or_not_supported(self):
        self._reject(
            "SELECT total_net_sales FROM product_sales "
            "WHERE market = 'Houston' OR market = 'Dallas'"
        )

    def test_having_on_dimension_rejected(self):
        self._reject(
            "SELECT market, total_net_sales FROM product_sales "
            "GROUP BY market HAVING market > 'A'"
        )


class TestPivot(SqlCase):
    def test_case_pivot_becomes_a_comparison(self):
        plan = to_plan(_PIVOT_SQL, self.model)
        self.assertIsInstance(plan, Comparison)
        self.assertEqual(plan.metric, "total_net_sales")
        self.assertEqual(plan.split_by, "iso_week")
        self.assertEqual(plan.period_field, "iso_year")
        self.assertEqual(plan.periods, [2025, 2026])
        self.assertEqual(plan.filters[0].field, "product_name")

    def test_plain_query_is_a_semantic_query(self):
        plan = to_plan(
            "SELECT market, total_net_sales FROM product_sales GROUP BY market", self.model
        )
        self.assertNotIsInstance(plan, Comparison)

    def test_single_period_is_not_a_pivot(self):
        # one CASE column isn't a pivot; a lone CASE isn't a bare column -> rejected
        with self.assertRaises(SemanticSqlError):
            to_plan(
                "SELECT iso_week, SUM(CASE WHEN iso_year=2025 THEN total_net_sales END) AS a "
                "FROM product_sales GROUP BY iso_week",
                self.model,
            )

    def test_mixed_metrics_is_not_a_pivot(self):
        with self.assertRaises(SemanticSqlError):
            to_plan(
                "SELECT iso_week, "
                "SUM(CASE WHEN iso_year=2025 THEN total_net_sales END) AS a, "
                "SUM(CASE WHEN iso_year=2026 THEN units_sold END) AS b "
                "FROM product_sales GROUP BY iso_week",
                self.model,
            )


class TestWindow(SqlCase):
    def test_lowers_to_base_subquery_with_window(self):
        sql, params, shape = compile_semantic_sql(_WINDOW_SQL, self.model, self.dialect)
        self.assertIn(") AS base", sql)            # wrapped over a base aggregate
        self.assertIn("LAG(", sql.upper())
        self.assertNotIn("product_sales", sql)     # virtual table replaced
        self.assertEqual(params, ["Cappuccino"])   # filter pushed into the base
        self.assertIsInstance(shape, QueryShape)
        self.assertEqual(shape.dimensions, ["iso_week"])
        self.assertEqual(shape.metrics, ["total_net_sales", "pct_change"])

    def test_window_requires_group_by(self):
        with self.assertRaises(SemanticSqlError):
            compile_semantic_sql(
                "SELECT total_net_sales, LAG(total_net_sales) OVER (ORDER BY iso_week) AS x "
                "FROM product_sales",
                self.model, self.dialect,
            )

    def test_window_rejects_unknown_column(self):
        with self.assertRaises(SemanticSqlError):
            compile_semantic_sql(
                "SELECT iso_week, LAG(item_net_sales) OVER (ORDER BY iso_week) AS x "
                "FROM product_sales GROUP BY iso_week",
                self.model, self.dialect,
            )


class TestEndToEnd(SqlCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.tmp = tempfile.TemporaryDirectory()
        cls.db = build_database(Path(cls.tmp.name) / "demo.db")

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def raw(self, sql, params=()):
        conn = sqlite3.connect(self.db)
        try:
            return conn.execute(sql, params).fetchall()
        finally:
            conn.close()

    def test_last_6_weeks_returns_only_recent_weeks(self):
        # the reported bug: "last six weeks" must NOT return the whole year.
        ir = self.ir(
            "SELECT iso_week, total_net_sales FROM product_sales "
            "WHERE product_name = 'Cappuccino' AND date >= last_period(6, 'week') "
            "GROUP BY iso_week ORDER BY iso_week"
        )
        sql, params = compile(ir, self.model, self.dialect)
        _, rows = SqliteExecutor(self.db).run(sql, params)
        weeks = [r[0] for r in rows]

        max_week = self.raw("SELECT MAX(iso_week) FROM fact_sales")[0][0]
        self.assertLessEqual(len(weeks), 7)  # ~6 weeks, not the whole year
        self.assertEqual(max(weeks), max_week)  # anchored at the latest data
        self.assertGreaterEqual(min(weeks), max_week - 6)

    def test_engine_runs_a_case_pivot(self):
        engine = Engine(self.model, _SqlPlanner(_PIVOT_SQL), self.dialect,
                        SqliteExecutor(self.db))
        result = engine.ask("compare cappuccino net sales 2025 vs 2026 by week")
        self.assertIsInstance(result.ir, Comparison)  # rendered as a comparison
        self.assertEqual(result.columns[0], "iso_week")
        self.assertEqual(len(result.columns), 3)  # week + one column per year
        self.assertTrue(result.rows)

    def test_derived_month_dimension_groups_by_month(self):
        sql, params, _ = compile_semantic_sql(
            "SELECT month, total_net_sales FROM product_sales "
            "GROUP BY month ORDER BY month",
            self.model, self.dialect,
        )
        self.assertIn("substr(date, 1, 7)", sql)  # derived expr, not a column
        _, rows = SqliteExecutor(self.db).run(sql, params)
        # grouped by calendar month (YYYY-MM): 24 months over two years, not ~104 weeks
        self.assertTrue(all(len(r[0]) == 7 and r[0][4] == "-" for r in rows))
        self.assertEqual(len(rows), 24)

    def test_engine_runs_a_window_query(self):
        engine = Engine(self.model, _SqlPlanner(_WINDOW_SQL), self.dialect,
                        SqliteExecutor(self.db))
        result = engine.ask("cappuccino net sales week over week percent change")
        self.assertIsInstance(result.ir, QueryShape)
        self.assertEqual(result.columns, ["iso_week", "total_net_sales", "pct_change"])
        self.assertIsNone(result.rows[0][2])  # first week has no prior -> NULL change
        self.assertTrue(any(r[2] is not None for r in result.rows[1:]))


if __name__ == "__main__":
    unittest.main()
