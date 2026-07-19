"""The narrate layer: choose_story derives a takeaway title, reference lines, point
annotations, and emphasis from (ir, spec, columns, rows) — deterministically, no LLM."""

import unittest

from text2sql.chat.charts import ChartSpec, choose_chart
from text2sql.chat.story import choose_story
from text2sql.engine.compare import Comparison
from text2sql.engine.ir import SemanticQuery


def sq(metrics, dims):
    return SemanticQuery(metrics=metrics, dimensions=dims)


class TestChooseStory(unittest.TestCase):
    def test_trend_title_reference_and_callouts(self):
        cols = ["month", "total_net_sales"]
        rows = [("2026-01-01", 4000.0), ("2026-02-01", 3500.0),
                ("2026-03-01", 4600.0), ("2026-12-01", 2918.0)]
        spec = ChartSpec("line", x="month", y=["total_net_sales"])
        story = choose_story(sq(["total_net_sales"], ["month"]), spec, cols, rows,
                             units={"total_net_sales": "usd"}, types={"month": "month"})
        self.assertIn("fell", story.title.lower())   # 4000 -> 2918
        self.assertIn("%", story.title)
        self.assertIn("avg", {r.role for r in story.references})
        roles = {a.role for a in story.annotations}
        self.assertEqual(roles, {"latest", "peak"})
        latest = next(a for a in story.annotations if a.role == "latest")
        self.assertEqual(latest.x, "Dec 2026")        # prettified to match the axis

    def test_topn_bar_title_and_emphasis(self):
        cols = ["product_name", "units_sold"]
        rows = [("Cappuccino", 4983), ("Vanilla Latte", 4056), ("Americano", 3491)]
        spec = ChartSpec("bar", x="product_name", y=["units_sold"], orientation="horizontal")
        story = choose_story(sq(["units_sold"], ["product_name"]), spec, cols, rows,
                             units={"units_sold": "count"})
        self.assertIn("Cappuccino leads", story.title)
        self.assertEqual(story.emphasis, "Cappuccino")

    def test_comparison_delta_title(self):
        cmp = Comparison.from_dict({"metric": "total_net_sales", "split_by": "month_of_year",
                                    "period_field": "iso_year", "periods": [2025, 2026]})
        cols = ["month_of_year", "total_net_sales_2025", "total_net_sales_2026"]
        rows = [(1, 100.0, 110.0), (2, 90.0, 99.0)]
        spec = choose_chart(cmp, cols, rows)
        story = choose_story(cmp, spec, cols, rows, units={"total_net_sales": "usd"})
        self.assertIn("2026 vs 2025", story.title)
        self.assertIn("+10%", story.title)            # (209-190)/190

    def test_table_and_number_have_no_story(self):
        self.assertIsNone(choose_story(sq(["x"], []), ChartSpec("number", y=["x"]),
                                       ["x"], [(1,)]))
        self.assertIsNone(choose_story(sq([], ["a", "b"]), ChartSpec("table"),
                                       ["a"], [("x",)]))

    def test_short_series_has_no_trend_story(self):
        cols = ["month", "total_net_sales"]
        rows = [("2026-01-01", 100.0), ("2026-02-01", 120.0)]  # < 3 points
        spec = ChartSpec("line", x="month", y=["total_net_sales"])
        self.assertIsNone(choose_story(sq(["total_net_sales"], ["month"]), spec, cols, rows))


if __name__ == "__main__":
    unittest.main()
