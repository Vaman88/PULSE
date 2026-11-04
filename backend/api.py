"""Small same-origin API and static site for the PULSE proof of concept."""
from __future__ import annotations

import hashlib
import hmac
import json
import re
import secrets
import gzip
from functools import lru_cache
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlparse

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from backend.demand import forecast
from backend.inventory import Medicine, inventory
from backend import intake
from backend.runner import read_kind
from model.prototype2.model import TARGETS

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "backend" / "data"
ACCOUNTS = DATA / "accounts.json"
PLANS = DATA / "plans.json"
INVENTORY = DATA / "inventory.sqlite3"
app = FastAPI(title="PULSE prototype signals", version="1.0")


def read(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def save(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def series() -> list[dict]:
    records = {}
    completed_through = read(DATA / "metadata.json").get("latest_prediction_date")
    if not completed_through:
        return []
    for key, value in read_kind("output").items():
        if value.get("model") != "prototype2":
            continue
        if completed_through and value["date"] > completed_through:
            continue
        if list(value.get("outputs", {})) != TARGETS:
            raise HTTPException(503, f"Incomplete prediction for sample {key}")
        if any(type(score) is not int or not 1 <= score <= 10
               for score in value["outputs"].values()):
            raise HTTPException(503, f"Invalid rating for sample {key}")
        records[(value["date"], key)] = {"sample_id": key, **value}
    return sorted(records.values(), key=lambda row: (row["date"], int(row["sample_id"])))


@app.get("/api/v1/signals/latest")
def latest():
    rows = series()
    if not rows:
        raise HTTPException(503, "No predictions have been generated")
    return rows[-1]


@app.get("/api/v1/signals/history")
def history(start: date, end: date):
    if end < start or (end - start).days > 3660:
        raise HTTPException(400, "Invalid timeframe")
    rows = [row for row in series() if start.isoformat() <= row["date"] <= end.isoformat()]
    return {"start": start, "end": end, "count": len(rows), "records": rows}


@app.get("/api/v1/signals/freshness")
def freshness():
    rows = series()
    if not rows:
        raise HTTPException(503, "No predictions have been generated")
    latest_row = rows[-1]
    metadata = read(DATA / "metadata.json")
    fda_rows = list(read_kind("fda").values())
    matching = [row for row in fda_rows if row["date"] == latest_row["date"]]
    news_rows = list(read_kind("news").values())
    matching_news = [row for row in news_rows if row["date"] == latest_row["date"]]
    snapshots = intake.collections(DATA)
    collected = snapshots[-1] if snapshots else None
    return {
        "model": "prototype2",
        "latest_prediction_date": latest_row["date"],
        "latest_execution_at": latest_row.get("executed_at") or metadata.get("generated_at"),
        "news_date": latest_row["date"] if any(row["news"] for row in matching_news) else None,
        "fda_record_date": matching[-1]["record_date"] if matching else None,
        "fda_carried_forward": bool(matching and matching[-1]["record_date"] != latest_row["date"]),
        "latest_collection_at": collected["collected_at"] if collected else None,
        "latest_collected_news_date": max((a["published_date"] for a in intake.collected_news(DATA)), default=None),
        "latest_collected_fda_date": max((r["date"] for r in collected["fda_records"]), default=None) if collected else None,
    }


DISEASE_WORDS = {
    "influenza": ("influenza", "flu"), "west_nile": ("west nile",),
    "dengue": ("dengue",), "measles": ("measles",),
    "pertussis": ("pertussis", "whooping cough"), "hepatitis_a": ("hepatitis a",),
    "salmonella": ("salmonella",), "mumps": ("mumps",),
    "tuberculosis": ("tuberculosis", "tb"), "mpox": ("mpox",),
    "chikungunya": ("chikungunya",), "avian_influenza": ("avian influenza", "bird flu", "h5n1"),
    "ev_d68": ("ev-d68", "enterovirus d68"), "cholera": ("cholera",),
}


@lru_cache(maxsize=1)
def news_provenance() -> dict[tuple[str, int], dict]:
    """Index source URLs already collected with the training/news archive."""
    path = ROOT / "data" / "train_test_dataset" / "news_provenance.jsonl.gz"
    result = {}
    if path.exists():
        with gzip.open(path, "rt", encoding="utf-8") as stream:
            for line in stream:
                row = json.loads(line)
                if row.get("url"):
                    result[(str(row.get("sample_id")), int(row.get("article_index", 0)))] = row
    return result


@app.get("/api/v1/news/relevant")
def relevant_news():
    current = latest()
    metadata = read(DATA / "metadata.json")
    execution_at = current.get("executed_at") or metadata.get("latest_execution_at") or metadata.get("generated_at")
    cutoff = date.fromisoformat(execution_at[:10] if execution_at else current["date"])
    collected_articles = intake.collected_news(DATA)
    collected_urls = {a["url"] for a in collected_articles}
    if collected_articles:
        collected_day = max(datetime.fromisoformat(a["collected_at"]).astimezone(intake.LOCAL).date()
                            for a in collected_articles)
        cutoff = max(cutoff, collected_day)
    candidates = []
    samples = list(read_kind("input").items())
    samples.extend(("collection", {"date": a["published_date"], "inputs": {"news": [a]}})
                   for a in collected_articles)
    for key, sample in samples:
        for index, article in enumerate(sample["inputs"]["news"]):
            provenance = news_provenance().get((str(article.get("sample_id", key)), index), {})
            published = provenance.get("published_at_raw")
            published_date = article.get("published_date") or (
                str(published)[:10] if isinstance(published, str) and re.match(r"\d{4}-\d{2}-\d{2}", published)
                else sample["date"])
            age = (cutoff - date.fromisoformat(published_date)).days
            if not 0 <= age <= 6:
                continue
            title = article["title"]
            haystack = (title + " " + article["body"]).lower()
            matches = [name for name, words in DISEASE_WORDS.items()
                       if any(re.search(r"\b" + re.escape(word) + r"\b", haystack) for word in words)]
            url = article.get("url") or provenance.get("url")
            if not matches or not url:
                continue
            if key != "collection" and url in collected_urls:
                continue  # Prefer the explicitly dated, newly retrieved version.
            score = max(current["outputs"].get(name + suffix, 0)
                        for name in matches for suffix in ("_1w", "_2w"))
            bucket = ("Today" if age == 0 else "Yesterday" if age == 1 else
                      "Two Days Ago" if age == 2 else "Earlier This Week")
            host = urlparse(url).hostname or ""
            source = provenance.get("source") or host.removeprefix("www.")
            candidates.append({"date": published_date, "title": title,
                               "source": source, "url": url, "diseases": matches,
                               "period": bucket, "score": score, "sample_id": key,
                               "collected_at": article.get("collected_at"),
                               "recovered": article.get("recovered", False),
                               "date_basis": article.get("date_basis")})
    candidates.sort(key=lambda row: (row["date"], row["score"], row["title"]), reverse=True)
    unique = {}
    for item in candidates:
        unique.setdefault(item["url"], item)
    return {"as_of": current["date"], "execution_at": execution_at,
            "articles": list(unique.values())}


class Credentials(BaseModel):
    username: str
    password: str


def password_hash(password: str, salt: bytes) -> str:
    return hashlib.scrypt(password.encode(), salt=salt, n=2**14, r=8, p=1, dklen=32).hex()


def account(request: Request) -> str:
    token = request.cookies.get("pulse_session", "")
    digest = hashlib.sha256(token.encode()).hexdigest()
    for name, info in read(ACCOUNTS).items():
        if token and hmac.compare_digest(info.get("session_hash", ""), digest):
            if datetime.fromisoformat(info["session_expiry"]) > datetime.now(timezone.utc):
                return name
    raise HTTPException(401, "Sign in required")


@app.post("/api/v1/auth/{action}")
def auth(action: str, credentials: Credentials, response: Response, request: Request):
    if action not in ("signup", "login"):
        raise HTTPException(404)
    name = credentials.username.strip().lower()
    if not re.fullmatch(r"[a-z0-9_-]{3,40}", name):
        raise HTTPException(400, "Username must be 3–40 letters, digits, _ or -")
    accounts = read(ACCOUNTS)
    if action == "signup":
        if name in accounts or len(credentials.password) < 12:
            raise HTTPException(400, "Username exists or password is too short")
        salt = secrets.token_bytes(16)
        accounts[name] = {"salt": salt.hex(), "password_hash": password_hash(credentials.password, salt)}
    else:
        info = accounts.get(name, {})
        if not info or not hmac.compare_digest(info["password_hash"], password_hash(credentials.password, bytes.fromhex(info["salt"]))):
            raise HTTPException(401, "Invalid credentials")
    token = secrets.token_urlsafe(32)
    accounts[name]["session_hash"] = hashlib.sha256(token.encode()).hexdigest()
    accounts[name]["session_expiry"] = (datetime.now(timezone.utc) + timedelta(days=7)).isoformat()
    save(ACCOUNTS, accounts)
    response.set_cookie("pulse_session", token, httponly=True, samesite="strict",
                        secure=request.url.scheme == "https", max_age=7*86400, path="/")
    return {"username": name}


@app.post("/api/v1/logout")
def logout(request: Request, response: Response):
    name = account(request)
    accounts = read(ACCOUNTS)
    accounts[name].pop("session_hash", None)
    accounts[name].pop("session_expiry", None)
    save(ACCOUNTS, accounts)
    response.delete_cookie("pulse_session", path="/", httponly=True, samesite="strict",
                           secure=request.url.scheme == "https")
    return {"ok": True}


@app.get("/api/v1/plan")
def plan(request: Request):
    return read(PLANS).get(account(request), {"recommendations": []})


@app.post("/api/v1/plan")
async def upload(request: Request):
    name = account(request)
    chunks = []
    size = 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > 5_000_000:
            raise HTTPException(413, "CSV exceeds 5 MB")
        chunks.append(chunk)
    body = b"".join(chunks)
    try:
        # Request bytes stay in memory; no upload or fitted model is written to disk.
        results = forecast(body.decode("utf-8-sig"), series())
    except (ValueError, UnicodeError) as error:
        raise HTTPException(400, str(error)) from error
    final = {"generated_at": datetime.now(timezone.utc).isoformat(), "recommendations": results}
    plans = read(PLANS)
    plans[name] = final
    save(PLANS, plans)
    return final


@app.get("/api/v1/inventory")
def list_inventory(request: Request):
    return {"items": inventory(INVENTORY, account(request))}


@app.post("/api/v1/inventory", status_code=201)
def add_medicine(medicine: Medicine, request: Request):
    return inventory(INVENTORY, account(request), medicine)


@app.put("/api/v1/inventory/{item_id}")
def edit_medicine(item_id: str, medicine: Medicine, request: Request):
    return inventory(INVENTORY, account(request), medicine, item_id)


app.mount("/public", StaticFiles(directory=ROOT / "frontend" / "public"), name="public")


@app.get("/live.js")
def live_script():
    return FileResponse(ROOT / "frontend" / "live.js", media_type="application/javascript")


@app.get("/")
def home():
    return FileResponse(ROOT / "frontend" / "index.html")
