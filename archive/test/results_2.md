# Archived synthetic-signal experiment

This folder keeps scripts and artifacts from an older synthetic pharmacy
signal experiment. The experiment joined dated model-derived signals to
synthetic daily drug sales, selected features using earlier rows, and fitted
per-drug XGBoost models for a 14-day sales horizon.

The original runner and its input tables are not in this local copy.
The plotting scripts are standalone generators for archived artifacts. The
current FastAPI service does not import them.
