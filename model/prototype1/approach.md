# Prototype 1: earlier research model

`prototype1.py` contains its training command, feature construction, and
`predict` function. `prototype1.pt` is the saved classifier checkpoint;
`fda_record_embeddings.json.gz` is a reusable FDA embedding cache. The
training source, article embeddings, and target labels under
`data/train_test_dataset/` were excluded from this local copy, so training
requires those files to be supplied separately.

## Training method

The script joins dated source examples to 28 disease/horizon rating labels by
sample ID. It loads existing 768-dimensional article embeddings and embeds
FDA shortage text with `google/embeddinggemma-2`. Features combine current
news and FDA means, the latest earlier FDA mean, recent news means, and
availability/count/recency values. It trains a shared neural classifier for
the 28 ten-class rating fields on chronological splits after the first
FDA-backed example. The last portion of each training segment is used for
early stopping. The final checkpoint is written to `prototype1.pt`.

With the research data and dependencies available, run from the project root:

```bash
python model/prototype1/prototype1.py
```

`--refresh-fda-embeddings` rebuilds the FDA cache. Running training writes
new local artifacts in this directory. The copied checkpoint already supports
inference when the EmbeddingGemma weights are available.

## Inference

```python
from model.prototype1.prototype1 import predict

ratings = predict(
    news_articles,
    latest_fda_records,
    previous_fda_records,
    days_since_previous_fda=14,
    recent_news_articles=recent_news_articles,
)
```

News inputs are article strings. FDA dictionaries use `record_kind`,
`generic_name`, `supplier`, `fda_initial_posting_date`, and
`date_basis`. Supply articles from the preceding two days as
`recent_news_articles`. The return value maps the 28 target names to
integer ratings. FDA shortage observations are context, not direct disease
incidence.
