"""Render the archived news-to-catalog architecture as a computation graph.

Counts are checked against the frozen test catalog. The token embedding and
FLAN-T5 blocks are schematic because upstream inference code and exact tensor
dimensions are not present in this repository checkout.
"""
from __future__ import annotations

from pathlib import Path

import matplotlib.pyplot as plt
import pandas as pd
from matplotlib.patches import Circle, FancyArrowPatch, FancyBboxPatch, Rectangle


HERE = Path(__file__).resolve().parent
CATALOG = HERE / "test_Signals" / "signals_2023_2025.csv.gz"
PNG = HERE / "signal_network_architecture.png"
SVG = HERE / "signal_network_architecture.svg"

BG = "#081722"
LANE = "#0D2532"
PANEL = "#133241"
STROKE = "#315362"
WHITE = "#EFF7F7"
MUTED = "#A2BCC3"
TEAL = "#4DDEC2"
BLUE = "#86BFFF"
VIOLET = "#C9A7FF"
GOLD = "#F5C978"


def catalog_counts() -> dict[str, int]:
    frame = pd.read_csv(CATALOG, usecols=["signal_id", "signal_origin", "entity_type"])

    def count(mask: pd.Series) -> int:
        return int(frame.loc[mask, "signal_id"].nunique())

    derived = frame.signal_origin.eq("derived_demand_output")
    counts = {
        "news": count(frame.signal_origin.eq("model_news_output")),
        "atc": count(derived & frame.entity_type.eq("therapeutic_class")),
        "cms": count(derived & frame.entity_type.eq("drug")),
        "external": count(frame.signal_origin.eq("model_external_state_feature")),
        "total": int(frame.signal_id.nunique()),
    }
    if tuple(counts.values()) != (20, 18, 1212, 62, 1312):
        raise ValueError(f"Catalog composition changed: {counts}")
    return counts


def panel(ax, x: float, y: float, w: float, h: float, accent: str,
          *, fill: str = PANEL) -> None:
    ax.add_patch(FancyBboxPatch(
        (x, y), w, h, boxstyle="round,pad=0.015,rounding_size=0.17",
        facecolor=fill, edgecolor=STROKE, linewidth=1.2, zorder=2,
    ))
    ax.add_patch(Rectangle((x + 0.035, y + 0.17), 0.055, h - 0.34,
                           facecolor=accent, edgecolor="none", zorder=3))


def edge(ax, start: tuple[float, float], end: tuple[float, float],
         color: str = TEAL, *, bend: float = 0.0, width: float = 1.9) -> None:
    ax.add_patch(FancyArrowPatch(
        start, end, arrowstyle="-|>", mutation_scale=13, linewidth=width,
        color=color, connectionstyle=f"arc3,rad={bend}", zorder=5,
    ))


def label(ax, x: float, y: float, value: str, *, size: float = 11,
          color: str = WHITE, weight: str = "normal", ha: str = "left") -> None:
    ax.text(x, y, value, fontsize=size, color=color, fontweight=weight,
            ha=ha, va="center", zorder=6)


def lane(ax, y: float, height: float, title: str, color: str) -> None:
    ax.add_patch(FancyBboxPatch(
        (0.45, y), 17.1, height, boxstyle="round,pad=0.01,rounding_size=0.22",
        facecolor=LANE, edgecolor="none", zorder=0,
    ))
    label(ax, 0.8, y + height - 0.26, title, size=10, color=color, weight="bold")


def main() -> None:
    c = catalog_counts()
    plt.rcParams.update({"font.family": "DejaVu Sans", "text.color": WHITE})
    fig, ax = plt.subplots(figsize=(17, 9.4), facecolor=BG)
    ax.set_facecolor(BG)
    ax.set_xlim(0, 18)
    ax.set_ylim(0, 10)
    ax.axis("off")

    label(ax, 0.55, 9.65, "PULSE  /  SIGNAL COMPUTATION GRAPH",
          size=10.5, color=TEAL, weight="bold")
    label(ax, 0.55, 9.18, "Text embeddings to 1,312 candidate signals",
          size=21, weight="bold")

    lane(ax, 6.48, 2.35, "01  TEXT ENCODER  ·  ARCHIVED UPSTREAM MODEL", BLUE)
    lane(ax, 3.55, 2.50, "02  DEMAND STATE HEADS  ·  NEWS + HISTORICAL DATA", VIOLET)
    lane(ax, 0.48, 2.60, "03  CATALOG COMPOSITION  ·  DATED SERIES", TEAL)

    # News-to-embedding computation lane. The matrix and transformer layers
    # communicate their roles; no unsupported width or layer count is claimed.
    panel(ax, 0.80, 6.89, 2.55, 1.52, BLUE)
    label(ax, 1.04, 7.92, "NEWS ARTICLES", size=11.5, weight="bold")
    label(ax, 1.04, 7.55, r"$x_t$  ·  dated text", size=10, color=MUTED)
    for offset in (0.0, 0.15, 0.30):
        ax.add_patch(Rectangle((2.52 + offset, 7.08 + offset * 0.25),
                               0.34, 0.36, facecolor=BLUE, alpha=0.28 + offset,
                               edgecolor=BLUE, linewidth=0.5, zorder=4))

    panel(ax, 3.72, 6.89, 3.12, 1.52, BLUE)
    label(ax, 3.95, 7.92, "TOKEN EMBEDDING", size=11.5, weight="bold")
    label(ax, 3.95, 7.55, r"$E(x_t) \in \mathbb{R}^{L\times d}$", size=10.5, color=MUTED)
    for row in range(4):
        for col in range(7):
            ax.add_patch(Rectangle(
                (5.48 + col * 0.145, 7.03 + row * 0.105), 0.09, 0.064,
                facecolor=BLUE, alpha=0.20 + 0.16 * ((row + col) % 4),
                edgecolor="none", zorder=4,
            ))

    panel(ax, 7.21, 6.89, 3.48, 1.52, BLUE)
    label(ax, 7.45, 7.92, "FLAN-T5-SMALL", size=11.5, weight="bold")
    label(ax, 7.45, 7.55, "encoder / decoder", size=10, color=MUTED)
    for index in range(3):
        ax.add_patch(FancyBboxPatch(
            (9.72 + index * 0.15, 7.15 + index * 0.12), 0.48, 0.38,
            boxstyle="round,pad=0.01,rounding_size=0.045",
            facecolor=BLUE, alpha=0.20 + 0.17 * index,
            edgecolor=BLUE, linewidth=0.7, zorder=4,
        ))

    panel(ax, 11.06, 6.89, 3.75, 1.52, TEAL)
    label(ax, 11.29, 7.92, f"{c['news']} NEWS SIGNALS", size=11.5, weight="bold")
    label(ax, 11.29, 7.55, r"$\mathbf{n}_t \in \mathbb{R}^{20}$  ·  monthly", size=10.4, color=MUTED)
    for row in range(2):
        for col in range(10):
            ax.add_patch(Circle(
                (11.39 + col * 0.17, 7.11 + row * 0.15), 0.043,
                facecolor=TEAL, alpha=0.35 + 0.055 * ((row + col) % 7),
                edgecolor="none", zorder=4,
            ))
    edge(ax, (3.35, 7.65), (3.72, 7.65), BLUE)
    edge(ax, (6.84, 7.65), (7.21, 7.65), BLUE)
    edge(ax, (10.69, 7.65), (11.06, 7.65), TEAL)

    # News variables fan into the two documented state classifiers. Each head
    # also uses its own historical utilization inputs.
    panel(ax, 1.05, 3.94, 7.35, 1.63, VIOLET)
    label(ax, 1.30, 5.19, f"{c['atc']} ATC THERAPEUTIC STATES",
          size=12, weight="bold")
    label(ax, 1.30, 4.78, r"$\mathbf{n}_t$  ⊕  Medicaid / RxNorm history",
          size=10.2, color=MUTED)
    label(ax, 1.30, 4.32, "18 logistic heads  →  three-state outputs",
          size=10.5, color=VIOLET)
    for col in range(9):
        for row in range(2):
            ax.add_patch(Circle((6.57 + col * 0.15, 4.24 + row * 0.15),
                                0.038, facecolor=VIOLET, alpha=0.45,
                                edgecolor="none", zorder=4))

    panel(ax, 9.17, 3.94, 7.72, 1.63, BLUE)
    label(ax, 9.42, 5.19, f"{c['cms']:,} CMS DRUG STATES",
          size=12, weight="bold")
    label(ax, 9.42, 4.78, r"$\mathbf{n}_t$  ⊕  Part D demand history",
          size=10.2, color=MUTED)
    label(ax, 9.42, 4.32, "five-state ridge ensemble  →  drug outputs",
          size=10.5, color=BLUE)
    for index in range(3):
        label(ax, 15.38 + index * 0.27, 4.23, "●", size=10, color=BLUE)
    edge(ax, (11.79, 6.89), (4.68, 5.57), VIOLET, bend=-0.16, width=1.9)
    edge(ax, (13.08, 6.89), (13.03, 5.57), BLUE, width=1.9)

    # Native-cadence public state/national features form a parallel input.
    panel(ax, 1.05, 0.89, 4.27, 1.72, GOLD)
    label(ax, 1.30, 2.22, f"{c['external']} EXTERNAL SERIES", size=11.5, weight="bold")
    label(ax, 1.30, 1.80, "disease · price · supply · other", size=9.8, color=MUTED)
    label(ax, 1.30, 1.34, r"$\mathbf{e}$  ·  dated public features", size=10, color=GOLD)

    panel(ax, 6.02, 0.89, 10.87, 1.72, TEAL)
    label(ax, 6.29, 2.25, "ALIGN BY SOURCE PERIOD  →  BUILD CANDIDATE CATALOG",
          size=11, weight="bold")
    label(ax, 6.29, 1.70,
          rf"$|\mathcal{{C}}| = {c['news']} + {c['atc']} + {c['cms']:,} + {c['external']} = {c['total']:,}$",
          size=18, color=WHITE, weight="bold")
    label(ax, 6.29, 1.20, "news   +   ATC states   +   CMS states   +   external",
          size=9.8, color=MUTED)

    edge(ax, (5.32, 1.75), (6.02, 1.75), GOLD, width=2.0)
    edge(ax, (4.74, 3.94), (8.42, 2.61), VIOLET, bend=0.04, width=1.9)
    edge(ax, (13.03, 3.94), (13.03, 2.61), BLUE, width=1.9)

    # The twenty news series are also preserved directly in the catalog.
    ax.plot([14.81, 17.31], [7.65, 7.65], color=TEAL, linewidth=1.8, zorder=4)
    ax.plot([17.31, 17.31], [7.65, 1.77], color=TEAL, linewidth=1.8, zorder=4)
    edge(ax, (17.31, 1.77), (16.89, 1.77), TEAL, width=1.8)

    label(ax, 0.55, 0.18,
          "Schematic embedding layer: upstream FLAN-T5 code and exact tensor dimensions are unavailable in this checkout.",
          size=8.6, color=MUTED)

    fig.savefig(PNG, dpi=185, facecolor=BG, bbox_inches="tight", pad_inches=0.18)
    fig.savefig(SVG, facecolor=BG, bbox_inches="tight", pad_inches=0.18)
    print(f"Wrote {PNG} and {SVG}; counts={c}")


if __name__ == "__main__":
    main()
