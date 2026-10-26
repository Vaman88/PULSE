# Oracle deployment files

This directory is a local copy of configuration and scripts for the Oracle
host. It is not needed to run the app locally. The copy operation did not
change the host, its services, or its `/opt/pulse` files.

| File | Purpose |
| --- | --- |
| `pulse-api.service` | Uvicorn service definition. |
| `pulse-daily.service`, `pulse-daily.timer` | Scheduled daily collection. |
| `pulse.nginx.conf`, `pulse-proxy-headers.conf` | Reverse proxy configuration. |
| `requirements.txt` | Python dependencies besides platform-specific PyTorch. |
| `preflight.py` | Calls source/model checks and inference. |
| `verify_live.py`, `verify_browser.py` | Checks the deployed API and browser. |
| `cleanup_test_account.py` | Removes a verification account from runtime storage. |

The original deployment runs the app from `/opt/pulse` with a separate
`/opt/pulse-venv`. Its live `backend/data/` directory contains private
state and was excluded from the local copy. The service and verification
scripts can contact or alter that deployment, so use them only when operating
the server intentionally. For local startup and API routes, see
[../../backend/README.md](../../backend/README.md).

The timer calls `python -m backend.daily` for dated CIDRAP and openFDA
collection. The model artifact is
`../../model/prototype2/three_day_model.joblib`; the EmbeddingGemma encoder
must also be available to the service user.
