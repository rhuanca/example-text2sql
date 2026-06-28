"""Render the synonyms 'resolution' map as a PNG.

Many user phrasings (gray, left) resolve to ONE canonical model name (orange
pill, right), tagged metric/dimension. This is the mechanism that lets the
planner map free wording onto the model's canonical names. All synonyms are
taken verbatim from models/sales.yml.

    uv run --with matplotlib python slides/synonyms_map.py
-> writes slides/assets/synonyms-map.png
"""

from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import FancyBboxPatch

ORANGE = "#DD5630"
GRAY = "#5C6470"

REPO = Path(__file__).resolve().parents[1]
OUT = REPO / "slides" / "assets" / "synonyms-map.png"
OUT.parent.mkdir(parents=True, exist_ok=True)

fig, ax = plt.subplots(figsize=(13, 6.2), dpi=200)
ax.set_xlim(0, 100)
ax.set_ylim(0, 50)
ax.axis("off")

PILL_CX, PILL_W, PILL_H = 80, 30, 7.6


def pill(cy, name, tag):
    ax.add_patch(FancyBboxPatch(
        (PILL_CX - PILL_W / 2, cy - PILL_H / 2), PILL_W, PILL_H,
        boxstyle="round,pad=0.1,rounding_size=2.2",
        linewidth=0, facecolor=ORANGE, zorder=3))
    ax.text(PILL_CX, cy + 1.2, name, ha="center", va="center", color="white",
            fontsize=13, fontweight="bold", zorder=4)
    ax.text(PILL_CX, cy - 1.7, tag, ha="center", va="center", color="#FBDDD0",
            fontsize=9, zorder=4)


def row(cy, synonyms, name, tag):
    ax.text(3, cy, synonyms, ha="left", va="center", color=GRAY,
            fontsize=11, style="italic", zorder=4)
    ax.annotate("", xy=(64, cy), xytext=(57, cy),
                arrowprops=dict(arrowstyle="-|>", color=ORANGE, lw=2.2,
                                mutation_scale=18), zorder=4)
    pill(cy, name, tag)


# header
ax.text(3, 48, "what users type", ha="left", va="center", color=GRAY,
        fontsize=12, fontweight="bold")
ax.text(PILL_CX, 48, "the one canonical name", ha="center", va="center",
        color=ORANGE, fontsize=12, fontweight="bold")

rows = [
    (42, '"revenue" · "net sales" · "sales" · "product sales"',
     "total_net_sales", "metric"),
    (33, '"orders" · "checks" · "transactions"',
     "traffic", "metric"),
    (24, '"territory" · "area"',
     "market", "dimension"),
    (15, '"channel" · "in-store vs online" · "where purchased"',
     "purchase_location", "dimension"),
    (6, '"week" · "week number"',
     "iso_week", "dimension"),
]
for cy, syns, name, tag in rows:
    row(cy, syns, name, tag)

fig.subplots_adjust(left=0, right=1, top=1, bottom=0)
fig.savefig(OUT, transparent=False, facecolor="white", bbox_inches="tight",
            pad_inches=0.1)
print(f"wrote {OUT}")
