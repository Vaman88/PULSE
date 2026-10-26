# Archived weekly risk and demand branch

- `pipeline_original.py`: original multi-command entry point.
- `risk_model.py`: logistic risk heads trained on synthetic event tags.
- `public_features.py`: dated news, calendar, and FDA feature construction.
- `demand_model.py`: weekly synthetic-clinic demand fitting with XGBoost.
- `trained_pulse_weekly/`: saved risk model, per-drug artifacts, and a dated
  forecast manifest from that branch.

The pipeline trained risk heads on synthetic event labels, then passed those
features into a synthetic weekly drug-demand model. Its original imports
include modules absent from this local tree, and its research datasets under
`data/` were excluded. It therefore cannot be rerun from this copy alone.
The active service calls `model/prototype2/` and uses
`backend/demand.py` for uploaded pharmacy histories.
