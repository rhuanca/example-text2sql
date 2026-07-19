"""Render the semantic model anatomy as a LAYERED STACK (PNG).

Five horizontal block-bars grouped into three abstraction bands, bottom-up:
  - the database   (teal):  tables + relationships, then facts
  - query surface  (amber): dimensions, then metrics
  - examples       (gray):  few-shot Q -> IR pairs
Each bar shows the block name + count on the left and a sample on the right;
a coloured bracket on the right groups the bars into their band.

    uv run --with matplotlib python slides/anatomy_stack.py
-> writes slides/assets/semantic-anatomy.png
"""

import sys
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import FancyBboxPatch

TEAL = "#1AA088"
AMBER = "#F2A20E"
GRAY = "#5C6470"

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
from text2sql.semantic.model import load_model

M = load_model(REPO / "models" / "sales.yml")  # counts read live so they never drift
OUT = REPO / "slides" / "assets" / "semantic-anatomy.png"
OUT.parent.mkdir(parents=True, exist_ok=True)

fig, ax = plt.subplots(figsize=(13, 6.5), dpi=200)
ax.set_xlim(0, 100)
ax.set_ylim(0, 50)
ax.axis("off")

LEFT, RIGHT, BH = 4, 79, 6.2
NAME_X, DESC_X = 8, 35


def bar(cy, color, name, desc):
    ax.add_patch(FancyBboxPatch(
        (LEFT, cy - BH / 2), RIGHT - LEFT, BH,
        boxstyle="round,pad=0.1,rounding_size=1.8",
        linewidth=0, facecolor=color, zorder=3))
    ax.text(NAME_X, cy, name, ha="left", va="center", color="white",
            fontsize=13, fontweight="bold", zorder=4)
    ax.text(DESC_X, cy, desc, ha="left", va="center", color="white",
            fontsize=10, zorder=4)


def band(y0, y1, color, label):
    ax.plot([82, 82], [y0, y1], color=color, lw=4.5,
            solid_capstyle="round", zorder=4)
    ax.text(85, (y0 + y1) / 2, label, ha="left", va="center", color=color,
            fontsize=12, fontweight="bold", zorder=4)


# bars, top (foundation) -> bottom (most abstract) — counts read from the live model
bar(45, TEAL,  f"tables ({len(M.tables)}) + relationships ({len(M.relationships)})",
    "fact_sales · dim_store · fact_budget  —  joined on store_id")
bar(38, TEAL,  f"facts ({len(M.facts)})",
    "raw measure columns  —  item_net_sales · quantity · budget_net_sales")
bar(28, AMBER, f"dimensions ({len(M.dimensions)})",
    "group-by / filter attributes  —  market · product · date · …")
bar(21, AMBER, f"metrics ({len(M.metrics)})",
    "named aggregations  —  total_net_sales = SUM(…) · units_sold · …")
bar(11, GRAY,  f"verified queries ({len(M.verified_queries)})",
    "Q → SQL pairs  —  teach and test the planner")

# bands (brackets + labels on the right)
band(34.6, 48.4, TEAL,  "the\ndatabase")
band(17.6, 31.4, AMBER, "query\nsurface")
band(7.6, 14.4, GRAY,  "examples")

fig.subplots_adjust(left=0, right=1, top=1, bottom=0)
fig.savefig(OUT, transparent=False, facecolor="white", bbox_inches="tight",
            pad_inches=0.1)
print(f"wrote {OUT}")
