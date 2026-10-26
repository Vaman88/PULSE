"""One entry point for Arkansas pharmacy research and dated weekly forecasts.

Run from repository root with ``encode``, ``research``, and ``forecast``.
``encode-extended`` prepares the separate observed-outcome risk study.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from datetime import timedelta
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.metrics import balanced_accuracy_score, brier_score_loss, roc_auc_score

from pulse.news_encoder import (encode_articles, load_embeddings, read_articles,
                                read_extended_arkansas_articles, save_embeddings)
from pulse.public_features import daily_public_features
from pulse.risk_model import RiskModel, event_targets
from pulse.demand_model import evaluate_drug, wape
from pulse.demand_model import drug_frame, fit_final_drug
from pulse.observed_monthly import run as run_observed_monthly
from pulse.quarterly_demand import (run_research as run_quarterly_research,
                                    run_forecast as run_quarterly_forecast,
                                    run_predict as run_quarterly_predict)
from pulse.observed_signal_risk import run as run_observed_signal_research
from pulse.demand_disruption_eval import run as run_step2_demand_disruption_eval
from pulse.step2_asof_snapshot import run as run_step2_evidence_snapshot

ROOT = Path(__file__).resolve().parents[2]
ARTICLES = ROOT / "data/targeted_additions/news_article_corpus/data/articles.jsonl.gz"
EMBEDDINGS = ROOT / "data/derived/pulse_flan_t5_small_articles.npz"
EXTENDED_EMBEDDINGS = ROOT / "data/derived/pulse_flan_t5_small_extended_title_body_fullarchive.npz"
EXTENDED_TITLE_EMBEDDINGS = ROOT / "data/derived/pulse_flan_t5_small_extended_titles_fullarchive.npz"
SALES = ROOT / "data/synthetic_pharmacy_data/arkansas_clinic_daily_pharmacy_sales.csv"
DERIVED = ROOT / "data/derived"
SHORTAGES = ROOT / "data/targeted_additions/fda_shortage_archive/data/fda_shortage_monthly.csv"
RISK_SIGNALS = DERIVED / "pulse_30_risk_signals_2023_2025.csv.gz"
HHS_MONTHLY = ROOT / "data/targeted_additions/hhs_medicaid_provider_spending_ndc/data/arkansas_practice_all_provider_ndc_monthly.csv.gz"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def run_research() -> None:
    sales = pd.read_csv(SALES, parse_dates=["date"])
    if sales.duplicated(["date", "drug_name"]).any() or (sales.units_sold < 0).any():
        raise ValueError("sales contains duplicate keys or negative counts")
    dates = pd.date_range(sales.date.min(), sales.date.max(), freq="D")
    if sales.drug_name.nunique() * len(dates) != len(sales):
        raise ValueError("sales panel is incomplete")
    features = daily_public_features(ROOT, EMBEDDINGS, dates)
    targets = event_targets(sales, dates)
    train_end, test_start = dates[int(.6 * len(dates))], dates[int(.8 * len(dates))]

    # Expanding-origin predictions for downstream training avoid using a risk
    # model fitted on the same future event labels as the origin being scored.
    signals = pd.DataFrame(index=dates, columns=targets.columns, dtype=float)
    train_boundaries = [dates[i] for i in range(240, int(.6 * len(dates)), 70)] + [train_end]
    for start, stop in zip(train_boundaries[:-1], train_boundaries[1:]):
        risk = RiskModel.fit(features, targets, start)
        subset = features.loc[(features.index >= start) & (features.index < stop)]
        signals.loc[subset.index] = risk.predict(subset)
        print(f"Risk backcast: {start.date()} to {(stop - timedelta(days=1)).date()}", flush=True)
    final_risk = RiskModel.fit(features, targets, train_end)
    signals.loc[signals.index >= train_end] = final_risk.predict(features.loc[features.index >= train_end])
    signals.index.name = "date"
    DERIVED.mkdir(parents=True, exist_ok=True)
    signals.to_csv(RISK_SIGNALS, compression={"method": "gzip", "mtime": 0})

    held = (dates >= test_start) & (dates <= dates[-1] - timedelta(days=30))
    risk_rows = []
    for name in targets:
        y = targets.loc[held, name].astype(int).to_numpy()
        p = signals.loc[held, name].to_numpy(float)
        row = {"signal": name, "n": len(y), "prevalence": float(y.mean()),
               "accuracy": float(((p >= .5) == y).mean()),
               "brier": brier_score_loss(y, p)}
        if len(np.unique(y)) == 2:
            row["balanced_accuracy"] = balanced_accuracy_score(y, p >= .5)
            row["auc"] = roc_auc_score(y, p)
        risk_rows.append(row)
    pd.DataFrame(risk_rows).to_csv(DERIVED / "pulse_risk_holdout_metrics.csv", index=False)

    metrics, predictions = [], []
    for drug, group in sales.groupby("drug_name", sort=True):
        score, predicted, _ = evaluate_drug(group, signals, train_end, test_start)
        metrics.append(score)
        predictions.append(predicted)
        print(f"Demand: {drug}: {score['sales_test_wape']:.3f} -> {score['signals_test_wape']:.3f}", flush=True)
    metric_table = pd.DataFrame(metrics)
    predicted_table = pd.concat(predictions, ignore_index=True)
    metric_table.to_csv(DERIVED / "pulse_weekly_per_drug_metrics.csv", index=False)
    predicted_table.to_csv(DERIVED / "pulse_weekly_test_predictions.csv.gz", index=False,
                           compression={"method": "gzip", "mtime": 0})

    event_tags = {
        "covid_summer_2025_outpatient_pulse": "outbreak",
        "dated_supply_outbreak_response": "other_disruption",
        "flu_2024_25": "outbreak",
        "amoxicillin_shortage": "other_disruption",
        "antibiotic_supply_pressure": "other_disruption",
        "h5n1_monitoring": "outbreak_monitoring",
    }
    event_results = []
    supply_rows = sales[sales.injected_event_tags.fillna("").str.contains(
        "dated_supply_outbreak_response", regex=False)]
    supply_keys = set(zip(supply_rows.drug_name, supply_rows.date))
    for tag, kind in event_tags.items():
        tagged = sales[sales.injected_event_tags.fillna("").str.contains(tag, regex=False)]
        if tagged.empty:
            continue
        tagged = tagged[tagged.date >= test_start]
        if tagged.empty:
            continue
        event_drugs = set(tagged.drug_name)
        event_keys = set(zip(tagged.drug_name, tagged.date))
        event_dates = set(tagged.date)
        ordered_dates = sorted(event_dates)
        episodes = 1 + sum((later - earlier).days > 1
                           for earlier, later in zip(ordered_dates[:-1], ordered_dates[1:]))
        sample = predicted_table[predicted_table.drug_name.isin(event_drugs)].copy()
        sample = sample[sample.apply(
            lambda row: any((row.drug_name, row.date + timedelta(days=i)) in event_keys
                            for i in range(1, 8)), axis=1)]
        if sample.empty:
            continue
        actual = sample.actual.to_numpy()
        result = {"event": tag, "kind": kind, "episode_count": episodes,
                              "event_start": str(ordered_dates[0].date()),
                              "event_end": str(ordered_dates[-1].date()),
                              "primary_drugs": sorted(event_drugs),
                              "drugs": len(event_drugs), "origins": len(sample),
                              "sales_wape": wape(actual, sample.sales_prediction.to_numpy()),
                              "signals_wape": wape(actual, sample.signals_prediction.to_numpy())}
        if kind == "outbreak":
            result["coincident_old_schedule_origins"] = int(sample.apply(
                lambda row: any((row.drug_name, row.date + timedelta(days=i)) in supply_keys
                                for i in range(1, 8)), axis=1).sum())
        event_results.append(result)
    summary = {
        "train_end_exclusive": str(train_end.date()), "test_start": str(test_start.date()),
        "n_drugs": int(sales.drug_name.nunique()), "n_risk_signals": len(signals.columns),
        "n_risk_holdout_days": int(held.sum()),
        "unweighted_drug_mean_sales_wape": float(metric_table.sales_test_wape.mean()),
        "unweighted_drug_mean_signals_wape": float(metric_table.signals_test_wape.mean()),
        "pooled_sales_wape": wape(predicted_table.actual.to_numpy(), predicted_table.sales_prediction.to_numpy()),
        "pooled_signals_wape": wape(predicted_table.actual.to_numpy(), predicted_table.signals_prediction.to_numpy()),
        "drugs_improved": int((metric_table.signals_test_wape < metric_table.sales_test_wape).sum()),
        "outbreak_episodes_in_test": sum(row["episode_count"] for row in event_results
                                         if row["kind"] == "outbreak"),
        "event_window_rule": "same drug has event tag on at least one of the next seven sales dates",
        "event_windows": event_results,
    }
    (DERIVED / "pulse_weekly_summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    print(json.dumps(summary, indent=2))


def run_forecast() -> None:
    import joblib

    if not RISK_SIGNALS.exists():
        raise FileNotFoundError("Run 'research' first to build expanding-origin risk signals")
    sales = pd.read_csv(SALES, parse_dates=["date"])
    dates = pd.date_range(sales.date.min(), sales.date.max(), freq="D")
    last_date = pd.Timestamp(dates[-1])
    features = daily_public_features(ROOT, EMBEDDINGS, dates)
    targets = event_targets(sales, dates)
    # Keep the risk-probability vintage used for the evaluated demand rows.
    # A later risk refit changes probability scales at inference; the matched
    # demand boosters have not been trained on that new stage interface.
    risk_train_end = dates[int(.6 * len(dates))]
    risk = RiskModel.fit(features, targets, risk_train_end)
    risk_path = ROOT / "model/pt1/trained/pulse_weekly/risk_model.joblib"
    risk_path.parent.mkdir(parents=True, exist_ok=True)
    signals = pd.read_csv(RISK_SIGNALS, parse_dates=["date"]).set_index("date")
    risk_at_origin = risk.predict(features.loc[[last_date]]).iloc[0]
    if last_date not in signals.index or not np.allclose(
            signals.loc[last_date].to_numpy(float), risk_at_origin.to_numpy(float),
            rtol=1e-6, atol=1e-8):
        raise ValueError("dated risk signals do not match the fixed risk-model vintage")
    joblib.dump(risk, risk_path)
    signals.loc[last_date] = risk_at_origin
    forecasts = []
    for i, (drug, group) in enumerate(sales.groupby("drug_name", sort=True)):
        model, selected, columns, estimate = fit_final_drug(group, signals)
        filename = f"drug_{i:03d}.json"
        model.save_model(risk_path.parent / filename)
        forecasts.append({"drug_name": drug, "forecast_next_7d_units": estimate,
                          "selected_risk_signals": selected, "feature_order": columns,
                          "booster_file": filename,
                          "booster_sha256": sha256_file(risk_path.parent / filename)})
    output = {"as_of_date": str(last_date.date()),
              "target_start": str((last_date + timedelta(days=1)).date()),
              "target_end": str((last_date + timedelta(days=7)).date()),
              "risk_model_train_end_exclusive": str(risk_train_end.date()),
              "scope": "30 products at one synthetic Arkansas clinic",
              "source_sales": SALES.relative_to(ROOT).as_posix(),
              "input_sha256": {
                  "sales": sha256_file(SALES), "articles": sha256_file(ARTICLES),
                   "embeddings": sha256_file(EMBEDDINGS), "fda_shortages": sha256_file(SHORTAGES),
                  "risk_signals": sha256_file(RISK_SIGNALS),
              },
              "code_sha256": {
                  "pipeline": sha256_file(ROOT / "model/pt1/pipeline.py"),
                  "demand_model": sha256_file(ROOT / "model/pt1/pulse/demand_model.py"),
                  "risk_model": sha256_file(ROOT / "model/pt1/pulse/risk_model.py"),
                  "public_features": sha256_file(ROOT / "model/pt1/pulse/public_features.py"),
                  "news_encoder": sha256_file(ROOT / "model/pt1/pulse/news_encoder.py"),
              },
              "risk_model_sha256": sha256_file(risk_path),
              "forecasts": forecasts}
    path = ROOT / "model/pt1/trained/pulse_weekly/forecast_2025_12_31.json"
    path.write_text(json.dumps(output, indent=2) + "\n")
    print(f"Saved {len(forecasts)} next-week synthetic-clinic forecasts to {path}")


def run_predict() -> None:
    """Reload saved boosters and reproduce the dated forecast without fitting."""
    import joblib
    from xgboost import XGBRegressor

    trained = ROOT / "model/pt1/trained/pulse_weekly"
    manifest_path = trained / "forecast_2025_12_31.json"
    manifest = json.loads(manifest_path.read_text())
    for name, relative in (("pipeline", "model/pt1/pipeline.py"),
                           ("demand_model", "model/pt1/pulse/demand_model.py"),
                           ("risk_model", "model/pt1/pulse/risk_model.py"),
                           ("public_features", "model/pt1/pulse/public_features.py"),
                           ("news_encoder", "model/pt1/pulse/news_encoder.py")):
        if sha256_file(ROOT / relative) != manifest["code_sha256"][name]:
            raise ValueError(f"saved forecast code changed: {name}")
    for name, path in (("sales", SALES), ("articles", ARTICLES),
                       ("embeddings", EMBEDDINGS), ("fda_shortages", SHORTAGES),
                       ("risk_signals", RISK_SIGNALS)):
        if sha256_file(path) != manifest["input_sha256"][name]:
            raise ValueError(f"saved forecast input changed: {name}")
    sales = pd.read_csv(SALES, parse_dates=["date"])
    as_of = pd.Timestamp(manifest["as_of_date"])
    if sales.date.max() != as_of:
        raise ValueError("saved forecast was trained for a different sales as-of date")
    dates = pd.date_range(sales.date.min(), as_of, freq="D")
    public = daily_public_features(ROOT, EMBEDDINGS, dates)
    if sha256_file(trained / "risk_model.joblib") != manifest["risk_model_sha256"]:
        raise ValueError("saved forecast risk model changed")
    risk = joblib.load(trained / "risk_model.joblib")
    signals = pd.read_csv(RISK_SIGNALS, parse_dates=["date"]).set_index("date")
    signals.loc[as_of] = risk.predict(public.loc[[as_of]]).iloc[0]
    output = []
    by_drug = {name: group for name, group in sales.groupby("drug_name")}
    for record in manifest["forecasts"]:
        drug = record["drug_name"]
        if drug not in by_drug:
            raise ValueError(f"missing drug in sales: {drug}")
        base, _, external = drug_frame(by_drug[drug], signals)
        x = pd.concat([base, external], axis=1)[record["feature_order"]].iloc[[-1]]
        if x.isna().any().any():
            raise ValueError(f"missing inference feature for {drug}")
        if sha256_file(trained / record["booster_file"]) != record["booster_sha256"]:
            raise ValueError(f"saved forecast booster changed for {drug}")
        booster = XGBRegressor()
        booster.load_model(trained / record["booster_file"])
        prediction = max(0., float(booster.predict(x)[0]))
        output.append({"drug_name": drug, "forecast_next_7d_units": prediction})
    print(json.dumps({"as_of_date": manifest["as_of_date"],
                      "target_start": manifest["target_start"],
                      "target_end": manifest["target_end"],
                      "scope": manifest["scope"],
                      "forecasts": output}, indent=2))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["encode", "encode-extended", "research", "forecast", "predict",
                                            "monthly-research", "quarterly-research", "quarterly-forecast",
                                            "quarterly-predict", "step2-observed-research",
                                            "step2-demand-eval", "step2-evidence"])
    parser.add_argument("--local-files-only", action="store_true")
    parser.add_argument("--train-end-exclusive", default="2024-10-19")
    parser.add_argument("--test-start", default="2025-05-26")
    parser.add_argument("--test-end-exclusive")
    parser.add_argument("--output-name", default="step2_observed_signal_risk")
    args = parser.parse_args()
    if args.command == "encode":
        articles = read_articles(ARTICLES)
        vectors = encode_articles(articles, local_files_only=args.local_files_only)
        save_embeddings(articles, vectors, EMBEDDINGS)
        print(f"Encoded {len(articles)} dated articles to {EMBEDDINGS} ({vectors.shape[1]} dimensions)")
    elif args.command == "encode-extended":
        articles = read_extended_arkansas_articles(ROOT)
        # Keep cached title-only vectors for unchanged records and refresh rows
        # whose body text is now retained (or whose IDs are not yet cached).
        meta, old_vectors = load_embeddings(EXTENDED_TITLE_EMBEDDINGS)
        old_lookup = {str(article_id): i for i, article_id in enumerate(meta.article_id)}
        body_present = articles.body.fillna("").astype(str).str.strip().ne("")
        missing = ~articles.article_id.astype(str).isin(old_lookup)
        refresh = body_present | missing
        vectors = np.empty((len(articles), old_vectors.shape[1]), dtype=np.float32)
        for row, article_id in enumerate(articles.article_id.astype(str)):
            if not refresh.iloc[row]:
                old_row = old_lookup[article_id]
                if pd.to_datetime(articles.published_at.iloc[row], utc=True).floor("s") != meta.published_at.iloc[old_row].floor("s"):
                    raise ValueError(f"Cached article date changed for {article_id}")
                vectors[row] = old_vectors[old_row]
        if refresh.any():
            vectors[refresh.to_numpy()] = encode_articles(
                articles.loc[refresh].reset_index(drop=True),
                local_files_only=args.local_files_only)
        save_embeddings(articles, vectors, EXTENDED_EMBEDDINGS)
        print(f"Prepared {len(articles)} title/body embeddings; refreshed {int(refresh.sum())} rows to {EXTENDED_EMBEDDINGS}")
    elif args.command == "research":
        run_research()
    elif args.command == "forecast":
        run_forecast()
    elif args.command == "predict":
        run_predict()
    elif args.command == "monthly-research":
        print(json.dumps(run_observed_monthly(HHS_MONTHLY, DERIVED), indent=2))
    elif args.command == "quarterly-research":
        print(json.dumps(run_quarterly_research(ROOT), indent=2))
    elif args.command == "quarterly-forecast":
        print(json.dumps(run_quarterly_forecast(ROOT), indent=2))
    elif args.command == "quarterly-predict":
        print(json.dumps(run_quarterly_predict(ROOT), indent=2))
    elif args.command == "step2-observed-research":
        print(json.dumps(run_observed_signal_research(
            ROOT, train_end_exclusive=args.train_end_exclusive,
            test_start=args.test_start, test_end_exclusive=args.test_end_exclusive,
            output_name=args.output_name), indent=2))
    elif args.command == "step2-demand-eval":
        summary = run_step2_demand_disruption_eval(ROOT, DERIVED / "experiments")
        print(json.dumps({"artifact": summary["artifact"],
                          "predictions_artifact": summary["predictions_artifact"],
                          "scores": summary["scores"]}, indent=2))
    elif args.command == "step2-evidence":
        print(json.dumps(run_step2_evidence_snapshot(), indent=2))


if __name__ == "__main__":
    main()
