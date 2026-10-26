# PULSE architecture summary

The live path is `backend/intake.py` -> `backend/daily.py` ->
`model/prototype2/predict.py` -> `backend/runner.py` storage ->
`backend/api.py` -> `frontend/`. `backend/demand.py` handles a separate
authenticated pharmacy CSV workflow.

`model/prototype2/model.py` defines the embedding, article classification,
feature, and rating-network components. The saved
`model/prototype2/three_day_model.joblib` supplies trained parameters.
`model/prototype1/` and `archive/` contain earlier research branches, not
the model called by daily collection.

The local copy excludes `data/` and `backend/data/`. Historical backfill,
embedding export, and retraining scripts refer to files in the omitted
research directory. Daily collection can build a new runtime store from
CIDRAP and openFDA, subject to network and model access. The FastAPI app
serves the static site and JSON endpoints even before local predictions exist.

Read [README.md](README.md) for the file map and startup instructions,
[backend/README.md](backend/README.md) for collection and API details, and
[model/prototype2/README.md](model/prototype2/README.md) for inference.
