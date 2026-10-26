"""Historical public features with conservative date lags.

The source archives lack complete publication vintages, so this is not a
verified point-in-time replay.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from .news_encoder import load_embeddings, read_articles


def daily_public_features(root: Path, embeddings_path: Path,
                          dates: pd.DatetimeIndex) -> pd.DataFrame:
    articles = read_articles(root / "data/targeted_additions/news_article_corpus/data/articles.jsonl.gz")
    meta, vectors = load_embeddings(embeddings_path)
    aligned = articles.merge(meta.assign(vector_row=np.arange(len(meta))), on="article_id",
                             suffixes=("", "_encoded"), validate="one_to_one")
    if not (aligned.published_at.dt.floor("s") == aligned.published_at_encoded).all():
        raise ValueError("article timestamps changed since encoding")
    vectors = vectors[aligned.vector_row.to_numpy()]
    text = (aligned.title.fillna("") + " " + aligned.body.fillna("")).str.lower()
    # Article categories are observable text features, never corpus query labels.
    terms = {
        "respiratory": r"\b(?:flu|influenza|rsv|respiratory|pneumonia)\b",
        "covid": r"\b(?:covid|sars.cov|coronavirus)\b",
        "shortage": r"\b(?:shortage|supply disruption|unavailable|backorder)\b",
        "access": r"\b(?:medicaid|insurance|clinic|hospital|access)\b",
        "avian": r"\b(?:h5n1|bird flu|avian influenza)\b",
    }
    daily = pd.DataFrame({"date": aligned.published_at.dt.tz_convert(None).dt.normalize(),
                          "article_count": 1.0})
    for name, regex in terms.items():
        daily[f"news_{name}"] = text.str.contains(regex, regex=True).astype(float)
    daily = pd.concat([daily, pd.DataFrame(vectors,
                    columns=[f"flan_{i:03d}" for i in range(vectors.shape[1])])], axis=1)
    numeric = daily.columns.drop("date")
    daily = daily.groupby("date")[numeric].sum().reindex(dates, fill_value=0)
    counts = daily["article_count"].rolling(30, min_periods=1).sum().shift(1).fillna(0)
    out = pd.DataFrame(index=dates)
    out["article_count_30d"] = counts
    for name in terms:
        out[f"news_{name}_30d"] = daily[f"news_{name}"].rolling(30, min_periods=1).sum().shift(1).fillna(0)
    denom = counts.clip(lower=1)
    embedded = daily[[f"flan_{i:03d}" for i in range(vectors.shape[1])]]
    values = embedded.rolling(30, min_periods=1).sum().shift(1).fillna(0).div(denom, axis=0)
    out = pd.concat([out, values], axis=1)
    # Month-end archived shortage states are conservatively delayed 45 days.
    # The archive may contain retrospective revisions; this is a research proxy.
    shortage_path = root / "data/targeted_additions/fda_shortage_archive/data/fda_shortage_monthly.csv"
    shortage = pd.read_csv(shortage_path, usecols=["month", "ndc9", "shortage_active"])
    shortage = shortage[shortage.shortage_active == 1].groupby("month").ndc9.nunique()
    shortage.index = pd.to_datetime(shortage.index) + pd.offsets.MonthEnd(0) + pd.Timedelta(days=45)
    out["fda_shortage_ndc_count"] = shortage.reindex(dates, method="ffill").fillna(0).to_numpy()
    day = dates.dayofyear.to_numpy()
    out["season_sin"] = np.sin(2 * np.pi * day / 365.25)
    out["season_cos"] = np.cos(2 * np.pi * day / 365.25)
    out.index.name = "date"
    return out.astype("float32")
