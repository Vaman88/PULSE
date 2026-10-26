"""Dated input store and inference through the unchanged prototype2 entry point."""
from __future__ import annotations

import argparse
import gzip
import json
import os
import sys
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
SOURCE = ROOT / "data" / "train_test_dataset"
STORE = ROOT / "backend" / "data"
SHARD_SIZE = 400  # The research dataset's ordinary shard size.


def validate_prediction(result: dict) -> dict[str, int]:
    """Reject incomplete, reordered, or out-of-range model outputs."""
    from model.prototype2.model import TARGETS

    ratings = result.get("ratings")
    if not isinstance(ratings, dict) or list(ratings) != TARGETS:
        raise ValueError("Prototype2 must return exactly its 28 ordered rating fields")
    if any(type(value) is not int or not 1 <= value <= 10
           for value in ratings.values()):
        raise ValueError("Prototype2 ratings must be integer values in 1..10")
    return ratings


def read_kind(kind: str) -> dict[str, dict]:
    result = {}
    seen_at = {}
    for path in sorted((STORE / kind).glob("*.json")):
        for key, value in json.loads(path.read_text(encoding="utf-8")).items():
            # A stopped rewrite can leave its old shard until the next retry.
            if key not in result or path.stat().st_mtime_ns >= seen_at[key]:
                result[key], seen_at[key] = value, path.stat().st_mtime_ns
    return result


def write_kind(kind: str, rows: dict[str, dict]) -> None:
    """Rewrite bounded date-ordered shards; remove only obsolete backend shards."""
    folder = STORE / kind
    folder.mkdir(parents=True, exist_ok=True)
    ordered = sorted(rows.items(), key=lambda pair: (pair[1]["date"], int(pair[0])))
    names = set()
    for offset in range(0, len(ordered), SHARD_SIZE):
        chunk = ordered[offset:offset + SHARD_SIZE]
        name = f"{chunk[0][1]['date']}_to_{chunk[-1][1]['date']}.json"
        if name in names:
            name = f"{chunk[0][1]['date']}_to_{chunk[-1][1]['date']}_part{offset // SHARD_SIZE + 1:02d}.json"
        names.add(name)
        path = folder / name
        temporary = path.with_suffix(".json.tmp")
        temporary.write_text(json.dumps(dict(chunk), ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(temporary, path)
    for path in folder.glob("*.json"):
        if path.name not in names:
            path.unlink()


def source_samples() -> tuple[dict[str, dict], dict[str, Path]]:
    samples, shards = {}, {}
    for path in sorted(SOURCE.glob("*.json")):
        for key, sample in json.loads(path.read_text(encoding="utf-8")).items():
            if key in samples:
                raise ValueError(f"Duplicate source sample ID {key}")
            date.fromisoformat(sample["date"])
            inputs = sample["inputs"]
            if set(inputs) != {"news", "fda_records"}:
                raise ValueError(f"Invalid source input schema: {key}")
            for article in inputs["news"]:
                if not article.get("title") or not article.get("body"):
                    raise ValueError(f"Incomplete news article: {key}")
            samples[key], shards[key] = sample, path
    return samples, shards


def grouped(samples: dict[str, dict]) -> dict[str, list[tuple[str, dict]]]:
    dates = defaultdict(list)
    for key, sample in samples.items():
        dates[sample["date"]].append((key, sample))
    for entries in dates.values():
        entries.sort(key=lambda pair: int(pair[0]))
    return dict(sorted(dates.items()))


def recordings(entries: list[tuple[str, dict]]) -> list[dict]:
    found = {}
    for _, sample in entries:
        for record in sample["inputs"]["fda_records"]["drug_shortage_first_observed"] or []:
            marker = record.get("record_id") or json.dumps(record, sort_keys=True)
            found[marker] = {"date": sample["date"], **record}
    return list(found.values())


def articles(entries: list[tuple[str, dict]]) -> list[dict]:
    return [{"date": sample["date"], "sample_id": key, **article}
            for key, sample in entries for article in sample["inputs"]["news"]]


def request_for(day: str, dates: dict[str, list[tuple[str, dict]]]) -> dict:
    target = date.fromisoformat(day)
    previous = [(when, recordings(entries)) for when, entries in dates.items()
                if when < day and recordings(entries)]
    previous_records = max(previous, default=(None, []))[1]
    current_records = recordings(dates.get(day, []))
    return {
        "target_date": day,
        "target_news": articles(dates.get(day, [])),
        "past_news": [article for offset in range(1, 4)
                      for article in articles(dates.get((target - timedelta(days=offset)).isoformat(), []))],
        "fda_recordings": previous_records + current_records,
    }


def cached_vectors(samples: dict[str, dict], shards: dict[str, Path], selected_ids: set[str]):
    """Read vectors by article text, checking position and dimensions."""
    import numpy as np
    from model.prototype2.model import DIM, article_text

    vectors = {}
    for path in sorted({shards[key] for key in selected_ids}):
        embedded = SOURCE / "embeddings" / (path.stem + ".json.gz")
        with gzip.open(embedded, "rt", encoding="utf-8") as stream:
            content = json.load(stream)
        if content["metadata"]["dimensions"] != DIM:
            raise ValueError(f"Invalid embedding dimensions in {embedded}")
        for key in selected_ids:
            if shards[key] != path:
                continue
            news = samples[key]["inputs"]["news"]
            slots = content["samples"][key]["news"]
            if len(news) != len(slots):
                raise ValueError(f"Embedding count differs for sample {key}")
            for index, (article, slot) in enumerate(zip(news, slots)):
                vector = slot.get("embedding")
                if slot["news_index"] != index or vector is None or len(vector) != DIM:
                    raise ValueError(f"Missing or misaligned embedding {key}[{index}]")
                text = article_text(article)
                value = np.asarray(vector, dtype=np.float32)
                if text in vectors and not np.allclose(vectors[text], value, atol=0.005):
                    raise ValueError(f"Conflicting embeddings for {key}[{index}]")
                vectors[text] = value
    return vectors


def fda_cached_vectors(samples: dict[str, dict], selected_ids: set[str]) -> dict:
    import numpy as np
    from model.prototype2.model import DIM, article_text, fda_body

    path = ROOT / "model" / "prototype1" / "fda_record_embeddings.json.gz"
    with gzip.open(path, "rt", encoding="utf-8") as stream:
        cache = json.load(stream)["samples"]
    vectors = {}
    for key in selected_ids & cache.keys():
        records = samples[key]["inputs"]["fda_records"]["drug_shortage_first_observed"] or []
        slots = cache[key]["records"]
        if len(records) != len(slots):
            raise ValueError(f"FDA embedding count differs for sample {key}")
        for record, slot in zip(records, slots):
            if slot["embedding"] is None or len(slot["embedding"]) != DIM:
                raise ValueError(f"Missing FDA embedding for sample {key}")
            text = article_text({"title": "FDA drug shortage record", "body": fda_body(record)})
            vectors[text] = np.asarray(slot["embedding"], dtype=np.float32)
    return vectors


def predict_with_cache(requests: list[dict], vectors: dict) -> list[dict]:
    """Call prototype2.predict while reusing aligned research embeddings."""
    import numpy as np
    from model.prototype2 import predict as prototype2
    from model.prototype2.model import article_text, embed_articles

    needed = {}
    for request in requests:
        for article in request["target_news"] + request["past_news"]:
            needed[article_text(article)] = article
        for record in request["fda_recordings"]:
            from model.prototype2.model import fda_body
            article = {"title": "FDA drug shortage record", "body": fda_body(record)}
            needed[article_text(article)] = article
    missing = {text: article for text, article in needed.items() if text not in vectors}
    if missing:
        encoded = embed_articles(list(missing.values()), device_name="cuda" if __import__("torch").cuda.is_available() else "cpu")
        vectors.update(zip(missing, encoded))

    def lookup(items: list[dict], device_name: str):
        return np.stack([vectors[article_text(article)] for article in items]).astype(np.float32)

    with patch.object(prototype2, "embed_articles", lookup):
        return [prototype2.predict(request, device="cpu") for request in requests]


def backfill(start: str, end: str) -> int:
    """Backfill actual prototype2 predictions for observed source dates."""
    if date.fromisoformat(end) < date.fromisoformat(start):
        raise ValueError("End date precedes start date")
    samples, shards = source_samples()
    dates = grouped(samples)
    selected_days = [day for day in dates if start <= day <= end]
    if not selected_days:
        raise RuntimeError(f"No historical inputs for {start} through {end}")
    context_start = (date.fromisoformat(start) - timedelta(days=3)).isoformat()
    context_ids = {key for day, entries in dates.items() if context_start <= day <= end
                   for key, _ in entries}
    earlier_fda_days = [day for day, entries in dates.items()
                        if day < context_start and recordings(entries)]
    if earlier_fda_days:
        context_ids.update(key for key, _ in dates[max(earlier_fda_days)])
    requests = [request_for(day, dates) for day in selected_days]
    vectors = cached_vectors(samples, shards, context_ids)
    vectors.update(fda_cached_vectors(samples, context_ids))
    results = predict_with_cache(requests, vectors)

    # Existing prototype1 files have no model marker and cannot be mixed in.
    old_outputs = read_kind("output")
    retain = {key for key, row in old_outputs.items() if row.get("model") == "prototype2"
              and not start <= row["date"] <= end}
    output_rows = {key: old_outputs[key] for key in retain}
    input_rows = {key: row for key, row in read_kind("input").items() if key in retain}
    news_rows = {key: row for key, row in read_kind("news").items() if key in retain}
    fda_rows = {key: row for key, row in read_kind("fda").items() if key in retain}
    for day, request, result in zip(selected_days, requests, results):
        key = dates[day][0][0]
        for old_key in [item for item, value in input_rows.items() if value["date"] == day and item != key]:
            input_rows.pop(old_key)
            output_rows.pop(old_key, None)
            news_rows.pop(old_key, None)
            fda_rows.pop(old_key, None)
        current = recordings(dates[day])
        latest = max((when for when in dates if when <= day and recordings(dates[when])), default=None)
        previous_predictions = [
            {"date": row["date"], "outputs": row["outputs"]}
            for offset in (1, 2)
            for row in output_rows.values()
            if row["date"] == (date.fromisoformat(day) - timedelta(days=offset)).isoformat()
        ]
        input_rows[key] = {"date": day, "inputs": {
            "news": request["target_news"], "past_news": request["past_news"],
            "fda_recordings": request["fda_recordings"],
            "previous_predictions": previous_predictions,
        }}
        output_rows[key] = {"date": day, "outputs": validate_prediction(result),
                            "disease_frequency": result["disease_frequency"],
                            "article_diseases": result["article_diseases"],
                            "model": "prototype2"}
        news_rows[key] = {"date": day, "news": request["target_news"]}
        fda_rows[key] = {"date": day, "record_date": latest, "records": recordings(dates[latest]) if latest else [],
                         "new_records": len(current)}
    for kind, rows in (("news", news_rows), ("fda", fda_rows), ("input", input_rows), ("output", output_rows)):
        write_kind(kind, rows)
    metadata = {"model": "prototype2", "backfill_start": start, "backfill_end": end,
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "latest_prediction_date": max(row["date"] for row in output_rows.values()),
                "prediction_count": len(output_rows),
                "rating_scale": "1-10 (unchanged research model)"}
    path = STORE / "metadata.json"
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(metadata, indent=2), encoding="utf-8")
    os.replace(temporary, path)
    return len(results)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--backfill", action="store_true")
    parser.add_argument("--start", default="2026-08-01")
    parser.add_argument("--end", default="2026-09-30")
    args = parser.parse_args()
    if args.backfill:
        print(f"Generated {backfill(args.start, args.end)} prototype2 predictions")
