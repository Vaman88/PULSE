"""Run the saved three-day news -> disease counts -> rating pipeline.

Input schema for predict(request):
  target_date: ISO date YYYY-MM-DD
  target_news: [{title, body}, ...]
  past_news: [{date, title, body, optional sample_id}, ...] from prior 3 days
  fda_recordings: [{date, record_kind, generic_name, supplier,
                    fda_initial_posting_date, date_basis, ...}, ...]

`date` on an FDA recording is its observation date in this pipeline. Pass all
recordings available by the target date so the latest earlier FDA date can be
found. Empty news/record lists are allowed. The result contains 28 ratings,
one count per disease, and disease labels for each supplied news article.

CLI: python model/prototype2/predict.py request.json
"""
from __future__ import annotations

import argparse
import json
import math
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import torch

if __package__:
    from .model import (DIM, TARGETS, RatingModel, classify_vectors,
                        embed_articles, fda_body, load_model, mean_vector)
else:
    from model import (DIM, TARGETS, RatingModel, classify_vectors,
                       embed_articles, fda_body, load_model, mean_vector)


def _parse_date(value: str) -> date:
    if not isinstance(value, str):
        raise TypeError("Dates must be ISO strings in YYYY-MM-DD format")
    return date.fromisoformat(value)


def _load_output_model(item: dict, device: torch.device) -> RatingModel:
    model = RatingModel(item["input_width"]).to(device)
    model.load_state_dict(item["model_state_dict"])
    return model.eval()


def predict(request: dict, device: str | None = None) -> dict:
    """Predict 28 ratings from today's news, three prior days, and FDA records."""
    if not isinstance(request, dict):
        raise TypeError("request must be a dictionary")
    target_day = _parse_date(request["target_date"])
    target_news = request.get("target_news", [])
    past_news = request.get("past_news", [])
    recordings = request.get("fda_recordings", [])
    if not all(isinstance(items, list) for items in (target_news, past_news, recordings)):
        raise TypeError("target_news, past_news, and fda_recordings must be lists")
    for article in target_news:
        if not isinstance(article, dict):
            raise TypeError("target_news articles must be title/body dictionaries")
    for article in past_news:
        if not isinstance(article, dict):
            raise TypeError("past_news articles must be dictionaries")
        observed = _parse_date(article["date"])
        if not target_day - timedelta(days=3) <= observed < target_day:
            raise ValueError("past_news dates must be within the 3 days before target_date")
    by_fda_date = defaultdict(list)
    for record in recordings:
        observed = _parse_date(record["date"])
        if observed > target_day:
            raise ValueError("FDA recordings cannot be dated after target_date")
        by_fda_date[observed].append(record)

    selected_device = device or ("cuda" if torch.cuda.is_available() else "cpu")
    bundle = load_model()
    classifier = bundle["disease_classifier"]
    checkpoint = bundle["rating_model"]
    output_device = torch.device(selected_device)
    output_model = _load_output_model(checkpoint, output_device)

    all_news = target_news + past_news
    news_vectors = (embed_articles(all_news, device_name=selected_device)
                    if all_news else np.zeros((0, DIM), dtype=np.float32))
    classified = classify_vectors(news_vectors, classifier) if all_news else []
    labels = classifier["labels"]
    daily_counts = defaultdict(lambda: np.zeros(len(labels), dtype=np.float32))
    per_article = []
    for i, (article, diseases) in enumerate(zip(all_news, classified)):
        observed = target_day if i < len(target_news) else _parse_date(article["date"])
        for label in diseases:
            daily_counts[observed][labels.index(label)] += 1
        per_article.append({"date": str(observed), "diseases": diseases})

    current_records = by_fda_date.get(target_day, [])
    earlier_dates = [day for day in by_fda_date if day < target_day]
    previous_date = max(earlier_dates) if earlier_dates else None
    previous_records = by_fda_date[previous_date] if previous_date else []
    fda_inputs = [("current", record) for record in current_records] + [
        ("previous", record) for record in previous_records]
    fda_articles = [{"title": "FDA drug shortage record", "body": fda_body(record)}
                    for _, record in fda_inputs if fda_body(record)]
    fda_vectors = (embed_articles(fda_articles, device_name=selected_device)
                   if fda_articles else np.zeros((0, DIM), dtype=np.float32))
    current_vecs, previous_vecs = [], []
    vector_index = 0
    for category, record in fda_inputs:
        if fda_body(record):
            (current_vecs if category == "current" else previous_vecs).append(fda_vectors[vector_index])
            vector_index += 1
    current_fda = mean_vector(current_vecs)
    previous_fda = mean_vector(previous_vecs)
    today_news = mean_vector(news_vectors[:len(target_news)])

    # Training averages each source sample's news, then averages sample means
    # across the preceding two dates. Optional sample_id preserves that layout.
    recent_groups = defaultdict(list)
    for offset, article in enumerate(past_news, start=len(target_news)):
        observed = _parse_date(article["date"])
        if observed >= target_day - timedelta(days=2):
            recent_groups[(observed, str(article.get("sample_id", observed)))].append(news_vectors[offset])
    recent = mean_vector([mean_vector(group) for group in recent_groups.values()])
    base = np.concatenate((
        today_news, current_fda, previous_fda, recent,
        np.asarray([bool(current_records), bool(previous_records),
                    math.log1p(len(current_records)),
                    min((target_day - previous_date).days, 3650) / 3650 if previous_date else 1.0,
                    bool(recent_groups)], dtype=np.float32),
    )).astype(np.float32)

    counts = np.zeros(len(labels), dtype=np.float32)
    for offset in range(4):
        counts += daily_counts.get(target_day - timedelta(days=offset), 0)
    mean = np.asarray(checkpoint["frequency_mean"], dtype=np.float32)
    scale = np.asarray(checkpoint["frequency_scale"], dtype=np.float32)
    features = np.concatenate((base, (counts - mean) / scale)).astype(np.float32)
    if len(features) != checkpoint["input_width"]:
        raise ValueError("Inference features do not match checkpoint width")
    with torch.inference_mode():
        ratings = (output_model(torch.from_numpy(features[None, :]).to(output_device))
                   .argmax(-1)[0].cpu().numpy() + 1)
    return {"target_date": str(target_day), "window_past_days": 3,
            "ratings": dict(zip(TARGETS, map(int, ratings))),
            "disease_frequency": dict(zip(labels, map(int, counts))),
            "article_diseases": per_article}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path, help="JSON request with target_date, news, FDA recordings")
    parser.add_argument("--device", choices=("cpu", "cuda"))
    args = parser.parse_args()
    request = json.loads(args.input.read_text(encoding="utf-8"))
    print(json.dumps(predict(request, device=args.device)))


if __name__ == "__main__":
    main()
