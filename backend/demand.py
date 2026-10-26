"""Temporary per-upload analysis. Only JSON-safe result summaries leave this module."""
from __future__ import annotations

import csv
import io
import math
from collections import defaultdict
from datetime import date, timedelta
from statistics import mean, median

import numpy as np
from scipy.stats import spearmanr
from sklearn.linear_model import Ridge
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

SIGNAL_COUNT = 28
REQUIRED = {"date", "drug_name", "unit", "starting_stock", "quantity_sold"}


def validate_csv(csv_text: str) -> dict[str, list[dict]]:
    reader = csv.DictReader(io.StringIO(csv_text))
    headers = set(reader.fieldnames or [])
    if not REQUIRED <= headers:
        raise ValueError("CSV must contain date, drug_name, unit, starting_stock, quantity_sold")
    allowed = REQUIRED | {"ending_stock", "current_stock", "expected_delivery"}
    if headers - allowed:
        raise ValueError("Unsupported CSV columns: " + ", ".join(sorted(headers - allowed)))
    grouped: dict[str, list[dict]] = defaultdict(list)
    units: dict[str, str] = {}
    canonical_names: dict[str, str] = {}
    seen: set[tuple[str, date]] = set()
    for line, row in enumerate(reader, 2):
        if not row or any(row.get(key) is None or not row[key].strip() for key in REQUIRED):
            raise ValueError(f"Row {line}: required values cannot be blank")
        try:
            day = date.fromisoformat(row["date"].strip())
            values = {key: float(row[key]) for key in ("starting_stock", "quantity_sold")}
            for key in ("ending_stock", "current_stock", "expected_delivery"):
                if key in headers and row[key].strip():
                    values[key] = float(row[key])
        except (ValueError, TypeError) as error:
            raise ValueError(f"Row {line}: use ISO dates and numeric inventory quantities") from error
        if any(not math.isfinite(value) or value < 0 for value in values.values()):
            raise ValueError(f"Row {line}: quantities must be finite and nonnegative")
        drug, unit = row["drug_name"].strip(), row["unit"].strip()
        if not drug or not unit:
            raise ValueError(f"Row {line}: drug_name and unit cannot be blank")
        normalized = drug.casefold()
        canonical_names.setdefault(normalized, drug)
        if normalized in units and units[normalized] != unit:
            raise ValueError(f"{drug}: measurement unit must be consistent ({units[normalized]} vs {unit})")
        units[normalized] = unit
        if (normalized, day) in seen:
            raise ValueError(f"{drug}: duplicate date {day.isoformat()}")
        seen.add((normalized, day))
        available = values["starting_stock"]
        if values["quantity_sold"] > available:
            raise ValueError(f"Row {line}: quantity_sold exceeds starting_stock")
        grouped[canonical_names[normalized]].append({"date": day, "unit": unit, **values})
    if not grouped:
        raise ValueError("CSV contains no inventory rows")
    return dict(grouped)


def _corr(a: np.ndarray, b: np.ndarray) -> float:
    if len(a) < 5 or np.std(a) == 0 or np.std(b) == 0:
        return 0.0
    value = spearmanr(a, b).statistic
    return float(value) if math.isfinite(value) else 0.0


def _feature_rows(rows: list[dict], signals: dict[date, dict], signal_names: list[str], lag: int):
    result = []
    for i, row in enumerate(rows):
        day = row["date"]
        signal_day = day - timedelta(days=lag)
        if signal_day not in signals:
            continue
        # Recent sales are historical at forecast time; never use today's outcome as a feature.
        prior = [r["quantity_sold"] for r in rows[:i] if r["date"] < day][-7:]
        if not prior:
            continue
        dt = day.timetuple().tm_yday
        x = [signals[signal_day][name] for name in signal_names]
        x += [mean(prior), math.sin(2 * math.pi * dt / 365.25), math.cos(2 * math.pi * dt / 365.25)]
        result.append((row, np.asarray(x), float(row["quantity_sold"])))
    return result


def _fit_evaluate(rows: list[dict], signals: dict[date, dict], signal_names: list[str], lag: int):
    samples = _feature_rows(rows, signals, signal_names, lag)
    if len(samples) < 12:
        return None
    split = max(8, int(len(samples) * .8))
    if split >= len(samples):
        split = len(samples) - 1
    train, valid = samples[:split], samples[split:]
    model = make_pipeline(StandardScaler(), Ridge(alpha=10.0))
    model.fit(np.stack([s[1] for s in train]), np.array([s[2] for s in train]))
    pred = np.maximum(0, model.predict(np.stack([s[1] for s in valid])))
    actual = np.array([s[2] for s in valid])
    baseline = np.full(len(valid), median(s[2] for s in train[-7:]))
    mae = float(np.mean(np.abs(actual - pred)))
    base_mae = float(np.mean(np.abs(actual - baseline)))
    return {"model": model, "samples": samples, "split": split, "mae": mae,
            "baseline_mae": base_mae, "validation_days": len(valid),
            "choice": "ridge" if mae < base_mae else "historical_baseline"}


def forecast(csv_text: str, signal_rows: list[dict]) -> list[dict]:
    grouped = validate_csv(csv_text)
    signals: dict[date, dict] = {}
    for entry in signal_rows:
        try:
            day = date.fromisoformat(entry["date"])
            outputs = entry["outputs"]
        except (KeyError, ValueError, TypeError):
            continue
        if (len(outputs) != SIGNAL_COUNT or any(type(v) is not int or not 1 <= v <= 10 for v in outputs.values())):
            continue
        signals[day] = outputs
    if not signals:
        raise ValueError("No complete historical records with the 28 final 1–10 disease signals are available")
    names = list(next(iter(signals.values())))
    if len(names) != SIGNAL_COUNT or any(list(v) != names for v in signals.values()):
        raise ValueError("Historical model output format is inconsistent; expected the same 28 final signals")
    results = []
    for drug, history in sorted(grouped.items()):
        history.sort(key=lambda r: r["date"])
        matched = [r for r in history if r["date"] in signals]
        if len(matched) < 20:
            raise ValueError(f"{drug}: only {len(matched)} exact-date signal matches; at least 20 are required (60+ recommended)")
        uncensored = []
        constrained = 0
        for row in matched:
            ending = row.get("ending_stock")
            capped = (ending == 0 if ending is not None else
                      row["starting_stock"] > 0 and row["quantity_sold"] >= row["starting_stock"])
            constrained += int(capped)
            if not capped:
                uncensored.append(row)
        if len(uncensored) < 12:
            raise ValueError(f"{drug}: only {len(uncensored)} uncensored matched days remain; at least 12 are needed for chronological evaluation")
        # Split chronologically before choosing signals or lag. Selection sees training rows only.
        candidate_rows = []
        for lag in (0, 1, 2):
            base = [r for r in uncensored if r["date"] - timedelta(days=lag) in signals]
            if len(base) < 12: continue
            cutoff = max(8, int(len(base) * .8))
            training = base[:cutoff]
            y = np.array([r["quantity_sold"] for r in training])
            scores = []
            for name in names:
                x = np.array([signals[r["date"] - timedelta(days=lag)][name] for r in training])
                c = _corr(x, y)
                scores.append((abs(c), name, c))
            chosen = sorted(scores, key=lambda x: (-x[0], x[1]))[:3]
            evaluated = _fit_evaluate(uncensored, signals, [x[1] for x in chosen], lag)
            if evaluated:
                candidate_rows.append((evaluated["mae"], lag, chosen, evaluated))
        if not candidate_rows:
            raise ValueError(f"{drug}: insufficient dated history for chronological validation")
        _, lag, selected, evaluation = min(candidate_rows, key=lambda x: (x[0], x[1]))
        forecast_start = history[-1]["date"]
        available_signal_dates = [day for day in signals if day <= forecast_start]
        if not available_signal_dates:
            raise ValueError(f"{drug}: no signal score was available on or before the final inventory date")
        latest_signal = max(available_signal_dates)
        censor_rate = constrained / len(matched)
        uplift = .05 if censor_rate >= .30 else .02 if censor_rate >= .10 else 0
        # Refit on uncensored observations where possible. If censoring leaves too few rows,
        # retain the full-data model but flag estimates as more uncertain.
        final_samples = _feature_rows(uncensored, signals,
                                      [x[1] for x in selected], lag)
        model = make_pipeline(StandardScaler(), Ridge(alpha=10.0))
        if evaluation["choice"] == "ridge" and len(final_samples) >= 12:
            model.fit(np.stack([s[1] for s in final_samples]), np.array([s[2] for s in final_samples]))
            next_day = forecast_start + timedelta(days=1)
            prior = [r["quantity_sold"] for r in matched if r["date"] <= forecast_start][-7:]
            doy = next_day.timetuple().tm_yday
            feature = [signals[latest_signal][x[1]] for x in selected] + [mean(prior), math.sin(2*math.pi*doy/365.25), math.cos(2*math.pi*doy/365.25)]
            raw = max(0., float(model.predict(np.asarray(feature)[None, :])[0]))
        else:
            raw = max(0., float(median(r["quantity_sold"] for r in (uncensored or matched)[-7:])))
        raw *= (1 + uplift)
        forecasts = [round(raw, 2)] * 14  # No future scores: use the latest signal-conditioned rate for each day.
        current = history[-1].get("current_stock", history[-1].get("ending_stock"))
        delivery = history[-1].get("expected_delivery", 0)
        safety_days = 3
        horizon = [sum(forecasts[:7]), sum(forecasts[:14])]
        recommendations = []
        for demand in horizon:
            recommendations.append(None if current is None else max(0, math.ceil(demand + raw*safety_days - current - delivery)))
        results.append({"drug_name": drug, "unit": history[-1]["unit"], "as_of": history[-1]["date"].isoformat(),
            "history": [{"date": row["date"].isoformat(),
                         "stock": row.get("ending_stock", row["starting_stock"] - row["quantity_sold"]),
                         "sales": row["quantity_sold"]}
                        for row in history if row["date"] >= forecast_start - timedelta(days=29)],
            "matched_days": len(matched), "selected_signals": [{"name": n, "correlation": round(c, 3), "lag_days": lag,
                "current_score": signals[latest_signal][n]} for _, n, c in selected],
            "daily_demand": round(raw, 2),
            "daily_forecasts": [{"date": (forecast_start + timedelta(days=i+1)).isoformat(), "demand": value}
                                for i, value in enumerate(forecasts)],
            "demand_7d": round(horizon[0]), "demand_14d": round(horizon[1]),
            "uncertainty": {"validation_mae": round(evaluation["mae"], 3), "baseline_mae": round(evaluation["baseline_mae"], 3),
                "validation_days": evaluation["validation_days"], "method": evaluation["choice"],
                "stock_constrained_days": constrained, "stock_constrained_rate": round(censor_rate, 3), "reserve_uplift_pct": int(uplift*100),
                "estimated_not_observed": True},
            "current_stock": current, "expected_delivery": delivery, "safety_stock_days": safety_days,
            "buy_7d": recommendations[0], "buy_14d": recommendations[1],
            "at_risk": current is not None and current + delivery < horizon[0],
            "stock_status": "conditional_estimate; provide current_stock for a grounded recommendation" if current is None else "estimated",
            "correlation_caveat": "Association is not evidence of causation; selected on training rows only."})
    return results
