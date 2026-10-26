# Backend and local app

Run all commands from the project root with Python 3.12. Install PyTorch for
your platform and the packages in `deploy/oracle/requirements.txt`. The
saved model also needs `google/embeddinggemma-2` in the Hugging Face cache
or access to download it.

```bash
python -m uvicorn backend.api:app --host 127.0.0.1 --port 8000
```

Open `http://127.0.0.1:8000/`. `backend/api.py` serves the static HTML,
JavaScript, and CSS in `frontend/` and exposes FastAPI's `/docs` page.
There is no frontend build step.

## File responsibilities

- `api.py`: signal and news endpoints, sign-up/login/logout, per-user saved
  plans, and static-file routes.
- `daily.py`: a dated collection and prediction run, with a lock and
  completion metadata.
- `intake.py`: CIDRAP RSS/article retrieval, openFDA shortage retrieval,
  provenance, and collection snapshots.
- `runner.py`: dated input/output shards, prediction validation, and
  historical backfill from the research archive.
- `demand.py`: pharmacy CSV validation and per-drug fitting.
- `test_frontend.py`, `test_intake.py`, `test_pharmacy.py`: route, intake,
  and planner tests.

The server's `backend/data/` directory was excluded from this copy. A
local run creates its own directory. It stores dated `news/`, `fda/`,
`input/`, and `output/` JSON shards; `collections/` snapshots;
`metadata.json`; and private `accounts.json` and `plans.json`. Do not
commit or share these local account and plan files. `api.py` has no signal
records to serve until a local prediction completes.

## Collect and predict

```bash
python -m backend.daily
python -m backend.daily --date YYYY-MM-DD
python -m backend.daily --collect-only --lookback 14
```

The default date is today in `America/Chicago`. `daily.py` fetches CIDRAP
topic feeds and article bodies, plus the openFDA Drug Shortages API. The
collection-only option saves source snapshots without revising predictions.
The predictor receives target-day news, the preceding three calendar days
of news, and dated FDA observations. `--force` replaces a completed local
date after corrected inputs. Network access is required for collection.

`runner.py` also offers
`python -m backend.runner --backfill --start YYYY-MM-DD --end YYYY-MM-DD`.
This reads `data/train_test_dataset/` and its embedding shards, which were
excluded from this copy. Backfill therefore needs the separately supplied
research archive. It selects observed input dates and does not fabricate
missing observations.

## JSON API

| Route | Purpose |
| --- | --- |
| `GET /api/v1/signals/latest` | Latest completed prediction. |
| `GET /api/v1/signals/history?start=YYYY-MM-DD&end=YYYY-MM-DD` | Completed dates in a bounded inclusive range. |
| `GET /api/v1/signals/freshness` | Prediction and source observation dates. |
| `GET /api/v1/news/relevant` | Recent keyword-matched articles with provenance. |
| `POST /api/v1/auth/signup`, `POST /api/v1/auth/login`, `POST /api/v1/logout` | Private portal sessions. |
| `GET /api/v1/plan`, `POST /api/v1/plan` | Read or replace the signed-in user's plan. |

Signal endpoints need at least one completed local prediction. The article
relevance route uses keyword matching; ratings come from the saved model.

## Pharmacy CSV workflow

Upload one UTF-8 CSV of up to 5 MB with:

```csv
date,drug_name,unit,starting_stock,quantity_sold,ending_stock,current_stock,expected_delivery
```

`date` uses `YYYY-MM-DD`. The first five columns are required; the last
three are optional. Each drug needs dated sales rows that overlap completed
local signal dates. The planner chooses a small set of signals using earlier
rows, tests a 0–2 day lag, and fits a standardized Ridge model. It uses a
trailing-sales median when that is the selected rule. Upload bytes and fitted
models stay in request memory; the saved plan retains estimates and a bounded
stock/sales history. This is a research demonstration, not purchasing advice.

Run local contract tests with:

```bash
python -m pytest backend/test_frontend.py backend/test_intake.py backend/test_pharmacy.py -q
node --test frontend/signals.test.cjs
```

The Node test needs Node.js. The Python tests need `pytest` and the app
dependencies. These tests use temporary stores and do not need the omitted
server runtime data.
