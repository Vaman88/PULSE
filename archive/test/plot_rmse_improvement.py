"""Plot the approximately 20% RMSE result in the exploratory signal test."""
from __future__ import annotations

import json
from pathlib import Path

import matplotlib.pyplot as plt
from matplotlib.ticker import MultipleLocator


HERE = Path(__file__).resolve().parent
SOURCE = HERE / "signal_experiment_metrics.json"
OUTPUT = HERE / "rmse_improvement.png"


def main() -> None:
    results = json.loads(SOURCE.read_text(encoding="utf-8"))
    baseline = results["persistence_baseline"]
    variant = results["best_variant"]
    selected = results["pooled_metrics"][variant]
    if baseline["n"] != selected["n"]:
        raise ValueError("RMSE comparison requires the same number of test origins")

    before = baseline["rmse"]
    after = selected["rmse"]
    reduction = 100 * (1 - after / before)
    candidate_count = results["signal_candidate_columns"]
    n = baseline["n"]

    plt.rcParams.update({"font.family": "DejaVu Sans", "text.color": "#172B3A"})
    fig, ax = plt.subplots(figsize=(8.6, 5.7), facecolor="white")
    fig.subplots_adjust(left=0.15, right=0.96, top=0.78, bottom=0.24)
    ax.set_facecolor("white")

    ax.plot([0, 1], [before, after], color="#16836F", linewidth=2.5, zorder=2)
    ax.scatter(0, before, s=115, color="#8293A0", edgecolor="white", linewidth=2, zorder=3)
    ax.scatter(1, after, s=135, color="#16836F", edgecolor="white", linewidth=2, zorder=3)
    ax.annotate(f"{before:.2f}", (0, before), xytext=(0, 14),
                textcoords="offset points", ha="center", fontsize=13,
                fontweight="bold", color="#172B3A")
    ax.annotate(f"{after:.2f}", (1, after), xytext=(0, -23),
                textcoords="offset points", ha="center", fontsize=13,
                fontweight="bold", color="#16836F")
    ax.text(0.51, 69, f"−{reduction:.1f}% relative RMSE", ha="center",
            fontsize=10.5, color="#536675")

    ax.set_xlim(-0.35, 1.35)
    ax.set_ylim(0, 82)
    ax.set_xticks([0, 1], ["Persistence forecast", "Signal XGBoost"], fontsize=11)
    ax.set_ylabel("RMSE (14-day units)", fontsize=11, labelpad=10)
    ax.yaxis.set_major_locator(MultipleLocator(20))
    ax.grid(axis="y", color="#E7ECEF", linewidth=0.9)
    ax.set_axisbelow(True)
    ax.tick_params(axis="both", length=0, pad=8, colors="#536675")
    for side in ("top", "right", "bottom"):
        ax.spines[side].set_visible(False)
    ax.spines["left"].set_color("#CDD6DC")
    ax.spines["left"].set_linewidth(0.8)

    fig.text(0.15, 0.92, "EXPLORATORY SYNTHETIC TEST", fontsize=9,
             fontweight="bold", color="#16836F")
    fig.text(0.15, 0.85, "RMSE fell 19.6% versus persistence", fontsize=17,
             fontweight="bold", color="#172B3A")
    fig.text(0.15, 0.08,
             f"{n:,} matched origins · top 15/drug from {candidate_count:,} candidates · WAPE-selected of 5 variants",
             fontsize=8.5, color="#687985")

    fig.savefig(OUTPUT, dpi=200, facecolor="white")
    print(f"Wrote {OUTPUT}; RMSE {before:.3f} → {after:.3f}; reduction {reduction:.2f}%")


if __name__ == "__main__":
    main()
