"""The MySQL dialect drops into the same compiler core and produces valid MySQL
text — backtick identifiers, %s placeholders, DATE_SUB / DATE_FORMAT date math (no
DATE_TRUNC). Compile-only; live execution against MySQL is a later phase."""

import unittest

from text2sql.engine.compiler import compile
from text2sql.engine.dialects.mysql import MysqlDialect
from text2sql.engine.dialects.postgres import PostgresDialect
from text2sql.engine.dialects.sqlite import SqliteDialect
from text2sql.engine.ir import SemanticQuery
from tests.util import load_sales_model


class TestMysqlSeam(unittest.TestCase):
    def setUp(self):
        self.model = load_sales_model()

    def test_backtick_quoting_and_placeholder(self):
        ir = SemanticQuery.from_dict({
            "metrics": ["total_net_sales"], "dimensions": ["market"],
            "filters": [{"field": "market", "op": "=", "value": "Houston"}],
        })
        sql, params = compile(ir, self.model, MysqlDialect())
        self.assertIn("%s", sql)
        self.assertNotIn("?", sql)
        self.assertNotIn('"', sql)                       # backticks, not double quotes
        self.assertIn("`dim_store`.`market` = %s", sql)
        self.assertEqual(params, ["Houston"])

    def test_relative_date_uses_date_sub(self):
        ir = SemanticQuery.from_dict({
            "metrics": ["total_net_sales"], "dimensions": [],
            "time": {"field": "date", "last_n_days": 30},
        })
        sql, _ = compile(ir, self.model, MysqlDialect())
        self.assertIn("DATE_SUB(", sql)
        self.assertIn("INTERVAL 29 DAY", sql)            # last 30 days = anchor - 29
        self.assertIn("MAX(`date`)", sql)                # data-anchored window

    def test_time_grains_compile_to_mysql(self):
        for dim, needle in [("month", "DATE_FORMAT(`fact_sales`.`date`, '%Y-%m-01')"),
                            ("week_start", "WEEKDAY(`fact_sales`.`date`)"),
                            ("month_of_year", "MONTH(`fact_sales`.`date`)")]:
            ir = SemanticQuery.from_dict({"metrics": ["total_net_sales"], "dimensions": [dim]})
            sql, _ = compile(ir, self.model, MysqlDialect())
            self.assertIn(needle, sql, dim)
            self.assertNotIn("substr(", sql)             # no SQLite string ops
            self.assertNotIn("weekday 1", sql)

    def test_same_model_month_compiles_three_ways(self):
        # one structured model dim -> three dialect-correct SQL strings.
        ir = SemanticQuery.from_dict({"metrics": ["total_net_sales"], "dimensions": ["month"]})
        lite, _ = compile(ir, self.model, SqliteDialect())
        pg, _ = compile(ir, self.model, PostgresDialect())
        my, _ = compile(ir, self.model, MysqlDialect())
        self.assertIn("start of month", lite)            # sqlite date()
        self.assertIn("date_trunc('month'", pg)          # postgres
        self.assertIn("DATE_FORMAT", my)                 # mysql
        self.assertEqual(len({lite, pg, my}), 3)         # genuinely different SQL


if __name__ == "__main__":
    unittest.main()
