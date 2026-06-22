"""The Postgres dialect must drop into the same compiler core and produce valid
Postgres text — %s placeholders, double-quoted identifiers, interval date math —
without any change to the IR or compiler. Execution against a live Postgres is a
later spec; here we only assert the generated SQL shape."""

import unittest

from text2sql.engine.compiler import compile
from text2sql.engine.dialects.postgres import PostgresDialect
from text2sql.engine.dialects.sqlite import SqliteDialect
from text2sql.engine.ir import SemanticQuery
from tests.util import load_sales_model


class TestPostgresSeam(unittest.TestCase):
    def setUp(self):
        self.model = load_sales_model()

    def test_placeholder_and_quoting(self):
        ir = SemanticQuery.from_dict(
            {
                "metrics": ["total_net_sales"],
                "dimensions": ["market"],
                "filters": [{"field": "market", "op": "=", "value": "Houston"}],
            }
        )
        sql, params = compile(ir, self.model, PostgresDialect())
        self.assertIn("%s", sql)
        self.assertNotIn("?", sql)
        self.assertIn('"dim_store"."market" = %s', sql)
        self.assertEqual(params, ["Houston"])

    def test_relative_date_uses_interval(self):
        ir = SemanticQuery.from_dict(
            {
                "metrics": ["total_net_sales"],
                "dimensions": [],
                "time": {"field": "date", "last_n_days": 30},
            }
        )
        sql, _ = compile(ir, self.model, PostgresDialect())
        self.assertIn("CURRENT_DATE - INTERVAL '30 days'", sql)

    def test_same_ir_differs_only_by_dialect(self):
        ir = SemanticQuery.from_dict(
            {
                "metrics": ["total_net_sales", "total_budget"],
                "dimensions": ["store_id"],
            }
        )
        pg, _ = compile(ir, self.model, PostgresDialect())
        lite, _ = compile(ir, self.model, SqliteDialect())
        # structure is identical (both produce the fan-out-safe CTE join)
        self.assertIn("agg_fact_sales", pg)
        self.assertIn("agg_fact_budget", pg)
        self.assertIn("agg_fact_sales", lite)
        self.assertEqual(pg, lite)  # no dialect-specific text in this query


if __name__ == "__main__":
    unittest.main()
