"""Audit whether the synthetic drivers are represented by available signals."""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from run_xgb_signals import signal_matrix

ROOT = Path(__file__).resolve().parents[1]
SALES = ROOT / "data/synthetic_pharmacy_data/arkansas_clinic_daily_pharmacy_sales.csv"
OUT = Path(__file__).resolve().parent / "signal_alignment_audit.md"


def main() -> None:
    sales = pd.read_csv(SALES, parse_dates=["date"])
    sales["tag"] = sales.injected_event_tags.fillna("").str.split("|")
    exploded = sales[["date", "drug_name", "units_sold", "tag"]].explode("tag")
    tags = []
    for tag, part in exploded[exploded.tag.ne("")].groupby("tag"):
        tagged_keys = set(zip(part.date, part.drug_name))
        sales_keys = list(zip(sales.date, sales.drug_name))
        tagged = sales_keys
        mask = [key in tagged_keys for key in sales_keys]
        on = sales.loc[mask, "units_sold"]
        off = sales.loc[[not x for x in mask], "units_sold"]
        tags.append({"tag": tag, "rows": int(len(on)),
                     "mean_units_on": float(on.mean()),
                     "mean_units_off": float(off.mean()),
                     "lift": float(on.mean() / max(off.mean(), 1e-9))})
    tags = sorted(tags, key=lambda x: (-x["lift"], x["tag"]))

    dates = pd.DatetimeIndex(sorted(sales.date.unique()))
    signal_frame, raw = signal_matrix(dates)
    variability = []
    for col in signal_frame:
        values = signal_frame[col].to_numpy(float)
        variability.append({"signal": col, "std": float(np.std(values)),
                            "nonzero_days": int(np.count_nonzero(values)),
                            "unique_values": int(np.unique(values).size)})
    variability.sort(key=lambda x: (-x["std"], x["signal"]))
    static = sum(x["unique_values"] <= 2 for x in variability)

    monthly = sales.groupby(sales.date.dt.to_period("M")).units_sold.sum()
    corr = []
    for col in signal_frame:
        series = signal_frame[col].groupby(signal_frame.index.to_period("M")).last()
        joined = pd.concat([monthly.rename("sales"), series.rename("signal")], axis=1).dropna()
        value = joined.sales.corr(joined.signal) if len(joined) > 3 else np.nan
        if np.isfinite(value): corr.append((abs(value), value, col))
    corr.sort(reverse=True)

    lines = ["# Synthetic-driver / model-signal alignment audit", "",
             "This is an audit of benchmark construction, not evidence of real Arkansas demand.", "",
             f"- Sales rows: {len(sales):,}",
             f"- Candidate signal columns: {signal_frame.shape[1]:,}",
             f"- Source signal rows used: {len(raw):,}",
             f"- Columns with two or fewer distinct daily values: {static:,}", "",
             "## Event-tag lift in the generated sales", "",
             "| Tag | Rows | Mean units on | Mean units off | Lift |",
             "|---|---:|---:|---:|---:|"]
    for row in tags:
        lines.append(f"| {row['tag']} | {row['rows']:,} | {row['mean_units_on']:.3f} | "
                     f"{row['mean_units_off']:.3f} | {row['lift']:.2f}x |")
    lines += ["", "## Most variable signal columns", "",
              "| Signal | Standard deviation | Nonzero days | Unique values |",
              "|---|---:|---:|---:|"]
    for row in variability[:25]:
        lines.append(f"| {row['signal']} | {row['std']:.4g} | {row['nonzero_days']:,} | {row['unique_values']:,} |")
    lines += ["", "## Strongest monthly associations with total sales", "",
              "These are descriptive correlations, not causal claims.", "",
              "| Signal | Correlation |", "|---|---:|"]
    for _, value, col in corr[:25]:
        lines.append(f"| {col} | {value:.3f} |")
    lines += ["", "## Interpretation", "",
              "The benchmark should not claim that every generated event is represented by a useful model signal. "
              "Annual or near-static columns cannot explain short outbreak windows, and duplicated derived columns "
              "should be deduplicated before counting independent evidence. A fair next benchmark either adds "
              "documented, time-varying event inputs or reports that the signals did not improve the event-driven "
              "synthetic demand."]
    OUT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(OUT)


if __name__ == "__main__":
    main()
