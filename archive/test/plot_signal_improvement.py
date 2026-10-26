"""Create a compact, reproducible WAPE comparison from benchmark metrics."""
from __future__ import annotations

import json
from pathlib import Path

import matplotlib.pyplot as plt
from matplotlib.ticker import MultipleLocator


HERE = Path(__file__).resolve().parent
METRICS_PATH = HERE / "publishable_benchmark_metrics.json"
OUTPUT_PATH = HERE / "signal_improvement.png"


def main() -> None:
    result = json.loads(METRICS_PATH.read_text(encoding="utf-8"))
    scores = result["untouched_test"]
    base = 100 * scores["sales_only"]["wape"]
    signals = 100 * scores["selected_signals"]["wape"]
    relative = 100 * (1 - signals / base)
    delta = signals - base

    plt.rcParams.update({
        "font.family": "DejaVu Sans",
        "text.color": "#172B3A",
        "xtick.color": "#536675",
        "ytick.color": "#536675",
    })
    fig, ax = plt.subplots(figsize=(8.6, 5.7), facecolor="white")
    fig.subplots_adjust(left=0.14, right=0.96, top=0.78, bottom=0.24)
    ax.set_facecolor("white")

    x = [0, 1]
    y = [base, signals]
    ax.plot(x, y, color="#16836F", linewidth=2.5, zorder=2)
    ax.scatter(x[0], y[0], s=115, color="#8293A0", edgecolor="white", linewidth=2, zorder=3)
    ax.scatter(x[1], y[1], s=135, color="#16836F", edgecolor="white", linewidth=2, zorder=3)

    ax.annotate(f"{base:.2f}%", (x[0], y[0]), xytext=(0, 15),
                textcoords="offset points", ha="center", fontsize=13,
                fontweight="bold", color="#172B3A")
    ax.annotate(f"{signals:.2f}%", (x[1], y[1]), xytext=(0, -25),
                textcoords="offset points", ha="center", fontsize=13,
                fontweight="bold", color="#16836F")
    ax.annotate(f"{delta:.2f} pp  ·  {relative:.1f}% relative",
                xy=(0.52, (base + signals) / 2), xytext=(0.52, 34.5),
                ha="center", va="center", fontsize=10, color="#536675",
                arrowprops={"arrowstyle": "-|>", "color": "#16836F", "lw": 1.2,
                            "connectionstyle": "arc3,rad=-0.25"})

    ax.set_xlim(-0.35, 1.35)
    ax.set_ylim(0, 38)
    ax.set_xticks(x, ["Sales only", "+ selected signals"], fontsize=11)
    ax.set_ylabel("WAPE (%)", fontsize=11, labelpad=10)
    ax.yaxis.set_major_locator(MultipleLocator(10))
    ax.grid(axis="y", color="#E7ECEF", linewidth=0.9)
    ax.set_axisbelow(True)
    ax.tick_params(axis="both", length=0, pad=8)
    for side in ("top", "right", "bottom"):
        ax.spines[side].set_visible(False)
    ax.spines["left"].set_color("#CDD6DC")
    ax.spines["left"].set_linewidth(0.8)

    fig.text(0.14, 0.92, "WAPE ON UNTOUCHED TEST DATA", fontsize=9,
             fontweight="bold", color="#16836F")
    fig.text(0.14, 0.85, "Signals improved the best headline result", fontsize=17,
             fontweight="bold", color="#172B3A")
    fig.text(0.14, 0.08,
             f"Synthetic benchmark · {scores['sales_only']['n']:,} matched origins · 5 of {result['signal_candidate_columns']:,} candidates selected per drug",
             fontsize=8.5, color="#687985")

    fig.savefig(OUTPUT_PATH, dpi=200, facecolor="white")
    print(f"Wrote {OUTPUT_PATH}; WAPE {base:.2f}% → {signals:.2f}% ({delta:.2f} pp, {relative:.2f}% relative)")


if __name__ == "__main__":
    main()
