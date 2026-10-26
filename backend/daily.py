"""Daily public-source intake and prototype2 inference.

Run at 23:30 America/Chicago for the current publication date. A failed run
can be repeated; the same date is replaced atomically within each data shard.
"""
from __future__ import annotations

import json
import os
from datetime import date, datetime, timedelta, timezone

from backend import intake
from backend.intake import LOCAL, parse_fda_date
from backend.runner import STORE, read_kind, validate_prediction, write_kind


def news_for(day: date) -> list[dict]:
    return intake.fetch_news(day, day, datetime.now(timezone.utc).isoformat())[0]


def fda_for(day: date) -> tuple[str | None, list[dict]]:
    records, _, _ = intake.fetch_fda(day, datetime.now(timezone.utc).isoformat())
    latest = max((r["date"] for r in records), default=None)
    return latest, [r for r in records if r["date"] == latest]


def acquire_lock():
    lock = STORE / ".daily.lock"
    STORE.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError as error:
        try:
            pid = int(lock.read_text().split()[0])
            os.kill(pid, 0)
        except (ValueError, IndexError, ProcessLookupError):
            lock.unlink(missing_ok=True)
            fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        else:
            raise RuntimeError("Another collection or daily run is in progress; inspect .daily.lock") from error
    with os.fdopen(fd, "w") as stream:
        stream.write(f"{os.getpid()} {datetime.now(timezone.utc).isoformat()}\n")
    return lock


def collect_only(day: date, lookback: int = 7) -> dict:
    lock = acquire_lock()
    try:
        snapshot = intake.collect(STORE, day, lookback)
        return {"collected_at": snapshot["collected_at"], "target_date": snapshot["target_date"],
                "news_fetch": snapshot["news_fetch"], "fda_fetch": snapshot["fda_fetch"],
                "prediction_records_changed": False}
    finally:
        lock.unlink(missing_ok=True)


def run(day: date, *, force: bool = False) -> dict:
    from model.prototype2.predict import predict

    key = day.isoformat()
    outputs = read_kind("output")
    existing = next((row for row in outputs.values()
                     if row["date"] == key and row.get("model") == "prototype2"), None)
    metadata_path = STORE / "metadata.json"
    completed = json.loads(metadata_path.read_text()).get("latest_prediction_date") if metadata_path.exists() else None
    if existing and completed and completed >= key and not force:
        return existing
    lock = acquire_lock()
    try:
        snapshot = intake.collect(STORE, day)
        news_rows, fda_rows = read_kind("news"), read_kind("fda")
        inputs = read_kind("input")
        fresh_news, history_news = intake.news_context(STORE, day, news_rows)
        record_date, fda_records, model_fda, new_fda = intake.fda_context(day, snapshot, fda_rows)
        previous = [{"date": row["date"], "outputs": row["outputs"]} for offset in (1, 2)
                    for row in outputs.values()
                    if row["date"] == (day - timedelta(days=offset)).isoformat()]
        request = {
            "target_date": key, "target_news": fresh_news,
            "past_news": history_news,
            "fda_recordings": model_fda,
            "previous_predictions": previous,
        }
        result = predict(request)
        ratings = validate_prediction(result)
        sample_id = next((sample_id for sample_id, row in inputs.items()
                          if row["date"] == key), None)
        if sample_id is None:
            sample_id = str(max([int(item) for item in inputs] + [5949]) + 1)
        news_rows[sample_id] = {"date": key, "news": fresh_news}
        fda_rows[sample_id] = {"date": key, "record_date": record_date,
                               "records": fda_records, "new_records": new_fda, "collected_at": snapshot["collected_at"],
                               "source_last_updated": snapshot["fda_fetch"]["last_updated"]}
        inputs[sample_id] = {"date": key, "inputs": {
            "news": fresh_news, "past_news": history_news,
            "fda_recordings": model_fda, "previous_predictions": previous},
                             "collection_at": snapshot["collected_at"]}
        output = {"date": key, "outputs": ratings,
                  "disease_frequency": result["disease_frequency"],
                  "article_diseases": result["article_diseases"], "model": "prototype2",
                  "executed_at": datetime.now(timezone.utc).isoformat()}
        outputs[sample_id] = output
        for kind, rows in (("news", news_rows), ("fda", fda_rows),
                           ("input", inputs), ("output", outputs)):
            write_kind(kind, rows)
        status = STORE / "metadata.json"
        metadata = json.loads(status.read_text()) if status.exists() else {}
        metadata.update({"latest_execution_at": output["executed_at"],
                         "latest_prediction_date": max(completed or key, key),
                          "prediction_count": len(outputs),
                         "latest_news_date": max((a["published_date"] for a in intake.collected_news(STORE)
                                                  if a["published_date"] <= key),
                                                 default=metadata.get("latest_news_date")),
                         "latest_collection_at": snapshot["collected_at"],
                         "latest_fda_record_date": record_date,
                         "model": "prototype2", "rating_scale": "1-10"})
        temporary = status.with_suffix(".json.tmp")
        temporary.write_text(json.dumps(metadata, indent=2), encoding="utf-8")
        os.replace(temporary, status)
        return output
    except Exception as error:
        path = STORE / "last_error.json"
        path.write_text(json.dumps({"date": key, "failed_at": datetime.now(timezone.utc).isoformat(),
                                    "error": f"{type(error).__name__}: {error}"}, indent=2), encoding="utf-8")
        raise
    finally:
        lock.unlink(missing_ok=True)


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", type=date.fromisoformat)
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--collect-only", action="store_true", help="Archive recovered inputs without revising predictions")
    parser.add_argument("--lookback", type=int, default=7)
    args = parser.parse_args()
    target = args.date or datetime.now(LOCAL).date()
    print(json.dumps(collect_only(target, args.lookback) if args.collect_only else run(target, force=args.force)))
