"""Time-series visualization contract: for each canonical time-series shape, the
chosen chart and its Altair encoding are pinned here. Nine scenarios over the sales
model (1-8) and qbo (9). Deterministic — synthetic rows, no DB or LLM.

Maps each shape to: the choose_chart decision, the plots.py builder the app renders,
and the key visual property (mark, axis, sort/labels, %-axis, clustered, series)."""

import unittest
from pathlib import Path

from text2sql.chat.charts import choose_chart
from text2sql.chat.plots import (
    PALETTE, SERIES_1, SERIES_2, comparison_long, line_chart, line_panel,
    stacked_bar, vertical_grouped_bar,
)
from text2sql.engine.compare import Comparison
from text2sql.engine.ir import SemanticQuery
from text2sql.engine.semantic_sql import QueryShape
from text2sql.semantic.model import load_model
from tests.util import load_sales_model

REPO_ROOT = Path(__file__).resolve().parents[1]


def _maps(model):
    return ({m.name: m.unit for m in model.metrics},
            {d.name: d.additive for d in model.dimensions},
            {d.name: d.type for d in model.dimensions})


SALES = load_sales_model()
QBO = load_model(REPO_ROOT / "models" / "qbo.yml")
S_UNITS, S_ADD, S_TYPES = _maps(SALES)
Q_UNITS, Q_ADD, Q_TYPES = _maps(QBO)


def sq(metrics, dims):
    return SemanticQuery(metrics=metrics, dimensions=dims)


class TestTimeSeriesViz(unittest.TestCase):
    # 1 — net sales by week -> line on an integer week axis (no relabel)
    def test_01_week_line(self):
        cols = ["iso_week", "total_net_sales"]
        rows = [(1, 782.75), (2, 790.0), (3, 794.25)]
        spec = choose_chart(sq(["total_net_sales"], ["iso_week"]), cols, rows,
                            units=S_UNITS, additive=S_ADD, types=S_TYPES)
        self.assertEqual((spec.kind, spec.x), ("line", "iso_week"))
        enc = line_chart(_frame(cols, rows), "iso_week", "total_net_sales",
                         x_type=S_TYPES["iso_week"]).to_dict()
        self.assertEqual(enc["mark"]["type"], "line")
        self.assertEqual(enc["encoding"]["x"]["field"], "iso_week")
        self.assertIsNone(enc["encoding"]["x"]["sort"])  # week axis not relabeled/reordered

    # 2 — net sales by month -> line with friendly labels + chronological sort
    def test_02_month_line_labels_and_sort(self):
        cols = ["month", "total_net_sales"]
        rows = [("2026-01", 3999.5), ("2026-02", 3463.75), ("2026-03", 3526.0)]
        spec = choose_chart(sq(["total_net_sales"], ["month"]), cols, rows,
                            units=S_UNITS, additive=S_ADD, types=S_TYPES)
        self.assertEqual(spec.kind, "line")
        enc = line_chart(_frame(cols, rows), "month", "total_net_sales",
                         x_type="month").to_dict()
        self.assertEqual(enc["encoding"]["x"]["sort"],
                         ["Jan 2026", "Feb 2026", "Mar 2026"])

    # 3 — net sales + units (different scale) -> one line panel per measure
    def test_03_mixed_scale_small_multiples(self):
        cols = ["iso_week", "total_net_sales", "units_sold"]
        rows = [(1, 782.75, 97), (2, 790.0, 99)]
        spec = choose_chart(sq(["total_net_sales", "units_sold"], ["iso_week"]),
                            cols, rows, units=S_UNITS, additive=S_ADD, types=S_TYPES)
        self.assertEqual(spec.kind, "line")
        self.assertEqual(spec.y, ["total_net_sales", "units_sold"])
        # different units -> the app renders a separate single-measure panel each
        self.assertNotEqual(S_UNITS["total_net_sales"], S_UNITS["units_sold"])
        for m in spec.y:
            panel = line_panel(_frame(cols, rows), "iso_week", m).to_dict()
            self.assertEqual(panel["mark"]["type"], "line")

    # 4 — one measure split by market over week -> stacked bar
    def test_04_split_over_time_stacked_bar(self):
        cols = ["iso_week", "market", "total_net_sales"]
        rows = [(1, "Houston", 50.0), (1, "Dallas", 30.0),
                (2, "Houston", 60.0), (2, "Dallas", 40.0)]
        spec = choose_chart(sq(["total_net_sales"], ["iso_week", "market"]), cols, rows,
                            units=S_UNITS, additive=S_ADD, types=S_TYPES)
        self.assertEqual((spec.kind, spec.orientation), ("bar", "stacked"))
        enc = stacked_bar(_frame(cols, rows), "iso_week", "market", "total_net_sales",
                          x_type=S_TYPES["iso_week"]).to_dict()["encoding"]
        self.assertEqual(enc["y"]["stack"], "zero")     # stacked, not overlaid
        self.assertEqual(enc["x"]["field"], "iso_week")
        self.assertEqual(enc["color"]["field"], "market")

    # 5 — % change (LAG) -> line; the pct panel gets a %-axis + zero rule
    def test_05_percent_change_axis(self):
        cols = ["iso_week", "total_net_sales", "pct_change"]
        rows = [(1, 412.25, None), (2, 420.75, 2.06), (3, 430.0, 2.2)]
        ir = QueryShape(metrics=["total_net_sales", "pct_change"], dimensions=["iso_week"])
        spec = choose_chart(ir, cols, rows, units=S_UNITS, additive=S_ADD, types=S_TYPES)
        self.assertEqual(spec.kind, "line")
        pct = line_panel(_frame(cols, rows), "iso_week", "pct_change",
                         percent=True).to_dict()
        self.assertIn("layer", pct)  # zero rule + line
        label_exprs = [l.get("encoding", {}).get("y", {}).get("axis", {}).get("labelExpr", "")
                       for l in pct["layer"]]
        self.assertTrue(any("%" in le for le in label_exprs))

    # 6 — 2025 vs 2026 by week -> vertical clustered columns
    def test_06_year_compare_by_week_clustered(self):
        cmp = Comparison.from_dict({
            "metric": "total_net_sales", "split_by": "iso_week",
            "period_field": "iso_year", "periods": [2025, 2026]})
        cols = ["iso_week", "total_net_sales_2025", "total_net_sales_2026"]
        rows = [(1, 709.5, 782.75), (2, 713.0, 790.0)]
        spec = choose_chart(cmp, cols, rows, units=S_UNITS, additive=S_ADD, types=S_TYPES)
        self.assertEqual((spec.kind, spec.orientation), ("bar", "clustered"))
        long = comparison_long(cmp, cols, rows)
        enc = vertical_grouped_bar(long, "iso_week", "iso_year", [2025, 2026],
                                   x_type=S_TYPES["iso_week"]).to_dict()["encoding"]
        self.assertEqual(enc["x"]["field"], "iso_week")       # vertical: category on x
        self.assertEqual(enc["xOffset"]["field"], "period")   # side-by-side, not stacked
        self.assertEqual(enc["color"]["scale"]["range"], [SERIES_1, SERIES_2])

    # 7 — 2025 vs 2026 by month_of_year -> clustered, Jan..Dec labels
    def test_07_year_compare_by_month_labels(self):
        cmp = Comparison.from_dict({
            "metric": "total_net_sales", "split_by": "month_of_year",
            "period_field": "iso_year", "periods": [2025, 2026]})
        cols = ["month_of_year", "total_net_sales_2025", "total_net_sales_2026"]
        rows = [(1, 3653.75, 3999.5), (2, 3123.25, 3463.75), (3, 3141.25, 3526.0)]
        spec = choose_chart(cmp, cols, rows, units=S_UNITS, additive=S_ADD, types=S_TYPES)
        self.assertEqual(spec.orientation, "clustered")
        long = comparison_long(cmp, cols, rows)
        enc = vertical_grouped_bar(long, "month_of_year", "iso_year", [2025, 2026],
                                   x_type=S_TYPES["month_of_year"]).to_dict()["encoding"]
        self.assertEqual(enc["x"]["sort"], ["Jan", "Feb", "Mar"])  # month-of-year -> names
        self.assertEqual(enc["xOffset"]["field"], "period")

    # 8 — last-6-weeks window -> line (few points)
    def test_08_window_trend_line(self):
        cols = ["iso_week", "total_net_sales"]
        rows = [(47, 699.25), (48, 703.5), (49, 709.5), (50, 712.0), (51, 715.0), (52, 720.0)]
        spec = choose_chart(sq(["total_net_sales"], ["iso_week"]), cols, rows,
                            units=S_UNITS, additive=S_ADD, types=S_TYPES)
        self.assertEqual(spec.kind, "line")
        enc = line_chart(_frame(cols, rows), "iso_week", "total_net_sales",
                         x_type=S_TYPES["iso_week"]).to_dict()
        self.assertEqual(enc["mark"]["type"], "line")

    # 9 — Revenue vs Expense by month (qbo, additive:false) -> multi-series line
    def test_09_contrasting_split_over_time_line(self):
        cols = ["month", "classification", "total_amount"]
        rows = [("2026-01", "Revenue", 100.0), ("2026-01", "Expense", 60.0),
                ("2026-02", "Revenue", 110.0), ("2026-02", "Expense", 65.0)]
        spec = choose_chart(sq(["total_amount"], ["month", "classification"]), cols, rows,
                            units=Q_UNITS, additive=Q_ADD, types=Q_TYPES)
        self.assertEqual((spec.kind, spec.series), ("line", "classification"))  # NOT stacked
        enc = line_chart(_frame(cols, rows), "month", "total_amount",
                         color="classification", x_type="month").to_dict()["encoding"]
        self.assertEqual(enc["color"]["field"], "classification")   # one line per class
        self.assertEqual(enc["color"]["scale"]["range"], PALETTE)


def _frame(cols, rows):
    import pandas as pd
    return pd.DataFrame(rows, columns=cols)


if __name__ == "__main__":
    unittest.main()
