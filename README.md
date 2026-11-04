# PULSE

PULSE is a Python service and static web app for exploratory public-health
signals. It collects CIDRAP articles and openFDA drug-shortage observations,
uses a saved model to produce ratings for 14 diseases at two horizons, and
serves them through FastAPI. A private portal estimates pharmacy demand from
an uploaded sales and stock CSV.

## Local file map

| Path | Contents |
| --- | --- |
| `backend/api.py` | FastAPI routes, authentication, saved plans, and static-file serving. |
| `backend/daily.py` | Daily collection and prediction command. |
| `backend/intake.py` | CIDRAP/openFDA retrieval and dated collection snapshots. |
| `backend/runner.py` | Historical backfill and JSON shard storage. |
| `backend/demand.py` | CSV validation and per-drug demand estimates. |
| `backend/test_*.py` | Backend and frontend contract tests. |
| `frontend/index.html`, `frontend/live.js`, `frontend/public/` | Browser interface and static assets. |
| `model/prototype2/` | Current saved model, feature builder, and inference entry point. |
| `model/prototype1/` | Earlier training script, checkpoint, and supporting artifacts. |
| `model/test/` | Embedding export and exploratory scripts. |
| `deploy/oracle/` | Server configuration and operational scripts. |
| `archive/` | Historical research code and artifacts, outside the current service path. |
| `pytest.ini`, `.gitignore`, `.gitattributes` | Project configuration. |

The server's `data/` and `backend/data/` directories were deliberately not
copied. The former held research inputs and labels; the latter held live
collections, predictions, accounts, and plans. No private account or plan
records are present in this local copy. The saved model artifacts are present
under `model/`, so direct Prototype 2 inference does not need the research
dataset. Historical backfill and Prototype 1 retraining require the omitted
research inputs. The API starts with an empty local store; signal endpoints
have no completed dates until a local daily run succeeds.

## Run locally

Use Python 3.12. From the project root, create a virtual environment and
install PyTorch for your platform, then the remaining dependencies:

```bash
python -m venv .venv
# Activate .venv, then:
python -m pip install torch
python -m pip install -r deploy/oracle/requirements.txt
python -m uvicorn backend.api:app --host 127.0.0.1 --port 8000
```

Open `http://127.0.0.1:8000/`. The current model also loads
`google/embeddinggemma-2` through Transformers; its weights must be cached
or downloadable from Hugging Face. The frontend has no build step.

For a current-day collection and prediction, run
`python -m backend.daily`. This fetches external source pages and writes a
new local `backend/data/` store. See [backend/README.md](backend/README.md)
for API routes, the CSV contract, and collection options. See
[model/prototype2/README.md](model/prototype2/README.md) for direct inference
and its saved-model training design.

The ratings and article counts are research outputs. They are not disease
incidence, probabilities, clinical advice, or purchasing instructions.

## Inventory screen

The Inventory tab in Workspace stores medicines and balances per account in
`backend/data/inventory.sqlite3`. It supports add/edit, archive/restore, search,
stock filters, sorting, pagination and CSV export. Each medicine keeps its original
stock unit. Back up the inventory database with the account store. Barcode scans,
AI-assisted imports, batch expiry and automatic forecast updates are future work.

The interface and API baseline match the deployed `pulseintelligence.tech` version
(`codex/repo-sync`); the requested repository's `origin/main` has a different frontend.
Focused checks: `python -m pytest backend/test_inventory.py -q`.

### Handheld barcode input

Select **Scan barcode** in Inventory, focus the scan field, and scan with a USB
or Bluetooth reader in HID keyboard mode. Configure an Enter suffix, or use
**Read barcode**. Use a 2D imager that supports GS1 DataMatrix for medicine codes.
Pulse normalizes UPC/EAN/GTIN product codes and extracts a leading GS1 AI (01)
GTIN, so different serial/lot payloads resolve to the same saved package.
Known codes open the saved medicine; unknown codes can link to an existing
medicine without a code or start a new entry with the code already filled in.
Nothing is saved until staff confirm the form. This does not query an external
product catalog, decode lot/expiry fields, or increment stock automatically.
Parser checks: `node frontend/barcode.test.cjs`.
