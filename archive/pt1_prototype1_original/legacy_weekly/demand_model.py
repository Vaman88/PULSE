"""Third stage: matched seven-day sales-only and top-five-risk XGBoost arms."""
from __future__ import annotations

import numpy as np
import pandas as pd
from xgboost import XGBRegressor

LAGS = (1, 2, 3, 7, 14, 21, 28, 56)
ROLLS = (7, 14, 28, 56)
HORIZON = 7


def drug_frame(drug_sales: pd.DataFrame, risks: pd.DataFrame) -> tuple[pd.DataFrame, pd.Series, pd.DataFrame]:
    s = drug_sales.sort_values("date").set_index("date")
    if s.index.has_duplicates:
        raise ValueError("duplicate drug/date sales")
    y = s.units_sold.astype(float)
    x = pd.DataFrame(index=s.index)
    # Forecasts originate after the dated clinic sales have been recorded.
    # The target begins tomorrow, so today's count is known at the origin.
    x["units_current"] = y
    for lag in LAGS:
        x[f"units_lag_{lag}"] = y.shift(lag)
    for window in ROLLS:
        x[f"units_mean_{window}"] = y.shift(1).rolling(window).mean()
        x[f"units_std_{window}"] = y.shift(1).rolling(window).std().fillna(0)
    x["weekday"] = s.index.dayofweek
    x["month"] = s.index.month
    x["day_sin"] = np.sin(2 * np.pi * s.index.dayofyear / 365.25)
    x["day_cos"] = np.cos(2 * np.pi * s.index.dayofyear / 365.25)
    x["current_price"] = s.unit_price_usd
    x["current_stockout"] = s.stockout_flag
    target = sum(y.shift(-i) for i in range(1, HORIZON + 1))
    external = risks.reindex(x.index)
    return x, target.rename("next_7d_units"), external


def select_five(risk: pd.DataFrame, target: pd.Series, train_mask: np.ndarray) -> list[str]:
    training_risk = risk.loc[train_mask]
    training_target = target.loc[train_mask]
    valid = training_risk.notna().all(axis=1) & training_target.notna()
    training_risk = training_risk.loc[valid]
    training_target = training_target.loc[valid]
    scores = training_risk.corrwith(training_target).abs()
    ranked = scores.replace([np.inf, -np.inf], np.nan).dropna()
    ranked = sorted(ranked.index, key=lambda name: (-ranked[name], name))
    selected: list[str] = []
    families: set[str] = set()
    for name in ranked:
        family = name.rsplit("_", 1)[0]
        if family in families:
            continue
        if any(abs(training_risk[name].corr(training_risk[chosen])) > .995
               for chosen in selected):
            continue
        selected.append(name)
        families.add(family)
        if len(selected) == 5:
            break
    return selected


def fit_xgb(x: pd.DataFrame, y: pd.Series) -> XGBRegressor:
    model = XGBRegressor(n_estimators=220, max_depth=2, learning_rate=.04,
                         min_child_weight=8, subsample=.85, colsample_bytree=.7,
                         reg_lambda=10, tree_method="hist", n_jobs=1,
                         random_state=20250915, objective="reg:squarederror")
    model.fit(x, y)
    return model


def wape(y: np.ndarray, p: np.ndarray) -> float:
    return float(np.abs(y - p).sum() / max(np.abs(y).sum(), 1))


def evaluate_drug(drug_sales: pd.DataFrame, risks: pd.DataFrame,
                  train_end: pd.Timestamp, val_end: pd.Timestamp) -> tuple[dict, pd.DataFrame, dict]:
    base, y, external = drug_frame(drug_sales, risks)
    dates = base.index
    # Targets must finish before the next partition begins.
    train = (dates >= risks.dropna().index.min()) & (dates + pd.Timedelta(days=HORIZON) < train_end)
    val = (dates >= train_end) & (dates + pd.Timedelta(days=HORIZON) < val_end)
    pretest = train | val
    test = (dates >= val_end) & y.notna()
    complete = base.notna().all(axis=1) & external.notna().all(axis=1) & y.notna()
    train &= complete
    val &= complete
    pretest &= complete
    test &= complete
    selected = select_five(external, y, train)
    if len(selected) != 5:
        raise ValueError("fewer than five varying risk signals")
    profiles = {"sales": list(base), "signals": [*base, *selected]}
    fitted = {}
    metrics = {}
    predictions = pd.DataFrame({"date": dates[test], "actual": y.loc[test].to_numpy()})
    for arm, columns in profiles.items():
        features = pd.concat([base, external], axis=1)[columns]
        validation_model = fit_xgb(features.loc[train], y.loc[train])
        val_pred = np.maximum(0, validation_model.predict(features.loc[val]))
        fitted[arm] = fit_xgb(features.loc[pretest], y.loc[pretest])
        test_pred = np.maximum(0, fitted[arm].predict(features.loc[test]))
        predictions[f"{arm}_prediction"] = test_pred
        metrics[f"{arm}_validation_wape"] = wape(y.loc[val].to_numpy(), val_pred)
        metrics[f"{arm}_test_wape"] = wape(y.loc[test].to_numpy(), test_pred)
        metrics[f"{arm}_test_mae"] = float(np.mean(np.abs(y.loc[test].to_numpy() - test_pred)))
    metrics["drug_name"] = drug_sales.drug_name.iloc[0]
    metrics["selected_signals"] = "|".join(selected)
    metrics["n_test"] = int(test.sum())
    predictions["drug_name"] = metrics["drug_name"]
    return metrics, predictions, fitted


def fit_final_drug(drug_sales: pd.DataFrame, risks: pd.DataFrame):
    """Fit all known seven-day targets and predict from the latest sales date."""
    base, y, external = drug_frame(drug_sales, risks)
    train = base.notna().all(axis=1) & external.notna().all(axis=1) & y.notna()
    selected = select_five(external, y, train)
    if len(selected) != 5:
        raise ValueError("fewer than five varying risk signals")
    columns = [*base, *selected]
    x = pd.concat([base, external], axis=1)[columns]
    model = fit_xgb(x.loc[train], y.loc[train])
    if not x.iloc[-1].notna().all():
        raise ValueError("latest forecast origin has missing features")
    forecast = max(0., float(model.predict(x.iloc[[-1]])[0]))
    return model, selected, columns, forecast
