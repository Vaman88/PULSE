# Prototype 2: current saved model

`predict(request)` in `predict.py` is the public inference entry point.
`model.py` defines the article encoder, disease classifier application,
feature construction helpers, and rating network.
`three_day_model.joblib` contains trained classifier and rating parameters.
The original training script and research examples are not in this local
copy, so this directory supports inference, not retraining.

## Training design represented by the artifact

The model uses `google/embeddinggemma-2` to embed article text and FDA
shortage records. Saved per-disease logistic classifiers assign zero or more
disease labels to each article. Their counts cover the target day and its
three preceding calendar days. A saved neural rating head uses the counts
with current/recent news embeddings, current/prior FDA embeddings, and
availability and recency features to produce 28 integer ratings: one- and
two-week fields for 14 diseases. The artifact includes the trained weights
and normalization needed at inference time. Training examples and labels
were in the omitted `data/` directory.

## Run inference

Install `numpy`, `torch`, `scikit-learn`, `joblib`, `packaging`, and
`transformers` as specified in `deploy/oracle/requirements.txt`. The
EmbeddingGemma weights must be cached or downloadable.

Save a request, for example `request.json`:

```json
{
  "target_date": "2026-10-20",
  "target_news": [{"title": "Dengue alert", "body": "Officials report dengue cases."}],
  "past_news": [{"date": "2026-10-19", "title": "Earlier report", "body": "Cases were investigated."}],
  "fda_recordings": [{"date": "2026-10-20", "record_kind": "shortage", "generic_name": "medicine name", "supplier": "supplier name", "fda_initial_posting_date": "2026-10-20", "date_basis": "2026-10-20"}]
}
```

```bash
python model/prototype2/predict.py request.json
```

Python callers use `from model.prototype2.predict import predict` and pass
the same dictionary. The three article/FDA lists may be empty. Past articles
need a date within the preceding three calendar days; FDA observations need
a date no later than the target. The returned dictionary has `target_date`,
`window_past_days`, `ratings`, `disease_frequency`, and
`article_diseases`. Article counts describe labels assigned to supplied
news, not disease incidence.
