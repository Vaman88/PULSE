# Model lineage

The active service calls `model.prototype2.predict.predict` from
`backend/daily.py` and `backend/runner.py`. Prototype 2 combines article
embeddings, saved disease classifiers, three-day article frequencies, and
FDA-shortage context in a saved rating network. Its code and artifact are in
`model/prototype2/`.

`model/prototype1/prototype1.py` contains an earlier training and inference
pipeline. The weekly pharmaceutical-demand branch is preserved in
`archive/pt1_prototype1_original/legacy_weekly/`; its original dependencies
and data are not all in this local copy. The private pharmacy planner in
`backend/demand.py` is a separate request-time model trained on an uploaded
CSV, rather than part of either saved public-health model.

See [README.md](README.md), [backend/README.md](backend/README.md),
[model/prototype1/approach.md](model/prototype1/approach.md), and
[model/prototype2/README.md](model/prototype2/README.md) for files, training
procedures, and use.
