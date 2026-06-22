import unittest

from text2sql.chat import app
from text2sql.chat.charts import ChartSpec


class TestAppHelpers(unittest.TestCase):
    def test_import_does_not_launch(self):
        # importing the module must not require a running Streamlit server
        self.assertTrue(hasattr(app, "main"))

    def test_to_frame(self):
        df = app.to_frame(["a", "b"], [(1, 2), (3, 4)])
        self.assertEqual(list(df.columns), ["a", "b"])
        self.assertEqual(len(df), 2)

    def test_chart_frame_line(self):
        spec = ChartSpec("line", x="iso_week", y=["total_net_sales"])
        df = app.chart_frame(spec, ["iso_week", "total_net_sales"], [(10, 37.47), (11, 49.96)])
        self.assertEqual(df.index.name, "iso_week")
        self.assertEqual(list(df.columns), ["total_net_sales"])

    def test_chart_frame_multiseries_pivot(self):
        spec = ChartSpec("line", x="iso_week", y=["total_net_sales"], series="market")
        rows = [
            (10, "Houston", 50.0), (10, "Dallas", 30.0),
            (11, "Houston", 60.0), (11, "Dallas", 40.0),
        ]
        df = app.chart_frame(spec, ["iso_week", "market", "total_net_sales"], rows)
        self.assertEqual(df.index.name, "iso_week")
        self.assertEqual(set(df.columns), {"Houston", "Dallas"})

    def test_safe_summarize_falls_back_on_error(self):
        class Boom:
            def summarize(self, *a):
                raise RuntimeError("no key")

        out = app.safe_summarize(Boom(), "q", ["a"], [(1,)])
        self.assertIn("1 row", out)


if __name__ == "__main__":
    unittest.main()
