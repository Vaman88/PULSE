"""Public-source snapshots, including explicitly dated late/recovered inputs.

Collection archives are additive. They never revise the requests or outputs
of an already completed prediction. Source dates are not observation times.
"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import date, datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from functools import lru_cache
from pathlib import Path
from urllib.parse import urlencode, urljoin
from urllib.request import Request, urlopen
from xml.etree import ElementTree
from zoneinfo import ZoneInfo

from bs4 import BeautifulSoup

BASE = "https://www.cidrap.umn.edu"
FDA = "https://api.fda.gov/drug/shortages.json"
TOPICS = ("influenza", "west nile", "dengue", "measles", "pertussis",
          "hepatitis", "salmonella", "mumps", "tuberculosis", "mpox",
          "chikungunya", "avian influenza", "enterovirus", "cholera")
LOCAL = ZoneInfo("America/Chicago")


def fetch(url: str) -> bytes:
    request = Request(url, headers={"User-Agent": "PULSE research proof-of-concept/1.0"})
    with urlopen(request, timeout=30) as response:
        return response.read()


def parse_fda_date(value: str) -> date:
    if len(value) == 8 and value.isdigit():
        return datetime.strptime(value, "%Y%m%d").date()
    if "/" in value:
        return datetime.strptime(value, "%m/%d/%Y").date()
    return date.fromisoformat(value[:10])


def fetch_news(start: date, end: date, collected_at: str) -> tuple[list[dict], dict]:
    listing = BeautifulSoup(fetch(BASE + "/rss-feeds"), "html.parser")
    feeds = {urljoin(BASE, anchor["href"])
             for anchor in listing.select('a[href$="/rss"]')
             if any(topic in anchor.get_text(" ", strip=True).lower() for topic in TOPICS)}
    if not feeds:
        raise RuntimeError("No CIDRAP topic RSS feeds found")
    found = {}
    for feed in sorted(feeds):
        root = ElementTree.fromstring(fetch(feed))
        channel = root.find("channel")
        if channel is None or not channel.findall("item"):
            raise RuntimeError(f"Empty or invalid CIDRAP feed: {feed}")
        for item in channel.findall("item"):
            published, link = item.findtext("pubDate"), item.findtext("link")
            if not published or not link:
                raise RuntimeError(f"CIDRAP item missing date or URL: {feed}")
            timestamp = parsedate_to_datetime(published)
            if timestamp.tzinfo is None:
                raise RuntimeError(f"CIDRAP publication time lacks timezone: {link}")
            published_day = timestamp.astimezone(LOCAL).date()
            if start <= published_day <= end:
                article = {"title": (item.findtext("title") or "").strip(),
                           "url": link, "published_date": published_day.isoformat(),
                           "published_at": timestamp.isoformat(), "published_at_raw": published,
                           "date_basis": "cidrap_rss_pubDate_America/Chicago",
                           "collected_at": collected_at,
                           "recovered": published_day < end}
                if link in found and found[link]["published_date"] != article["published_date"]:
                    raise RuntimeError(f"Conflicting CIDRAP publication dates: {link}")
                found[link] = article
    for link, article in found.items():
        page = BeautifulSoup(fetch(link), "html.parser")
        body = page.select_one(".field--name-field-body")
        text = body.get_text(" ", strip=True) if body else ""
        if not article["title"] or len(text) < 100:
            raise RuntimeError(f"Unable to extract complete article: {link}")
        article["body"] = text
    return sorted(found.values(), key=lambda a: (a["published_date"], a["url"])), {
        "feeds_checked": len(feeds), "window_start": start.isoformat(),
        "window_end": end.isoformat(), "articles": len(found)}


def fetch_fda(day: date, collected_at: str) -> tuple[list[dict], list[dict], dict]:
    raw, total, meta = [], None, None
    while total is None or len(raw) < total:
        url = FDA + "?" + urlencode({"sort": "package_ndc:asc", "limit": 1000, "skip": len(raw)})
        payload = json.loads(fetch(url))
        page = payload.get("results")
        current_meta = payload.get("meta", {})
        page_total = current_meta.get("results", {}).get("total")
        if not isinstance(page, list) or type(page_total) is not int or page_total < 0:
            raise RuntimeError("Invalid openFDA shortage response or missing total")
        if total is None:
            total, meta = page_total, current_meta
        if page_total != total or current_meta.get("last_updated") != meta.get("last_updated"):
            raise RuntimeError("openFDA changed during pagination; retry collection")
        if not page and len(raw) < total:
            raise RuntimeError("Incomplete openFDA pagination")
        raw.extend(page)
        if len(raw) > total:
            raise RuntimeError("openFDA returned more records than its total")
    records = []
    for item in raw:
        dated = {field: parse_fda_date(item[field])
                 for field in ("update_date", "change_date", "initial_posting_date") if item.get(field)}
        if not dated:
            raise RuntimeError("FDA record lacks all usable source dates")
        # Parse real dates locally: API fields contain MM/DD/YYYY strings.
        when = max(dated.values())
        if when > day:
            continue
        basis = next(field for field, value in dated.items() if value == when)
        records.append({
            "date": when.isoformat(), "record_kind": item.get("update_type") or "shortage",
            "generic_name": item.get("generic_name") or item.get("proprietary_name"),
            "supplier": item.get("company_name"), "package_ndc": item.get("package_ndc"),
            "fda_initial_posting_date": str(dated["initial_posting_date"]) if "initial_posting_date" in dated else None,
            "date_basis": when.isoformat(), "source_date_field": basis,
            "source_update_date": str(dated["update_date"]) if "update_date" in dated else None,
            "source_change_date": str(dated["change_date"]) if "change_date" in dated else None,
            "status": item.get("status"), "availability": item.get("availability"),
            "collected_at": collected_at, "recovered": when < day,
            "source_record_sha256": hashlib.sha256(json.dumps(item, sort_keys=True).encode()).hexdigest(),
        })
    return records, raw, {"last_updated": meta.get("last_updated"), "total": total,
                           "fetched": len(raw), "eligible": len(records)}


def collect(store: Path, day: date, lookback: int = 7) -> dict:
    if not 3 <= lookback <= 30:
        raise ValueError("Lookback must be between 3 and 30 days")
    now = datetime.now(timezone.utc)
    if day > now.astimezone(LOCAL).date():
        raise ValueError("Cannot collect a future prediction date")
    stamp = now.isoformat()
    news, news_meta = fetch_news(day - timedelta(days=lookback), day, stamp)
    records, raw, fda_meta = fetch_fda(day, stamp)
    snapshot = {"schema_version": 1, "target_date": day.isoformat(), "collected_at": stamp,
                "historical_reconstruction": False,
                "note": "Source publication/update dates; retrieved now, not proof of historical availability.",
                "news": news, "fda_records": records, "fda_raw": raw,
                "news_fetch": news_meta, "fda_fetch": fda_meta}
    folder = store / "collections"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / (now.strftime("%Y%m%dT%H%M%S%fZ") + ".json")
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)
    return snapshot


@lru_cache(maxsize=32)
def _read_collection(path: Path, modified_ns: int) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def collections(store: Path, since: date | None = None) -> list[dict]:
    paths = sorted((store / "collections").glob("*.json"))
    if not paths:
        return []
    # Public/current inference windows do not need to repeatedly parse years of
    # raw FDA archives. Old snapshots remain on disk for audit/reconstruction.
    newest = datetime.strptime(paths[-1].name[:8], "%Y%m%d").date()
    cutoff = (since or newest - timedelta(days=7)).strftime("%Y%m%d")
    return [_read_collection(path, path.stat().st_mtime_ns) for path in paths if path.name[:8] >= cutoff]


def collected_news(store: Path, since: date | None = None) -> list[dict]:
    articles = {}
    for snapshot in collections(store, since):
        for article in snapshot["news"]:
            articles[article["url"]] = article
    return list(articles.values())


def news_context(store: Path, day: date, saved: dict) -> tuple[list[dict], list[dict]]:
    # A corrected RSS date overrides the archived article's grouping without
    # rewriting the original request. Title matching also links URL-less archives.
    def title_key(article):
        return re.sub(r"\s+", " ", article["title"]).strip().casefold()
    articles = {(article.get("url") or title_key(article)): dict(article, date=row["date"], sample_id=key)
                for key, row in saved.items() for article in row["news"]}
    for article in collected_news(store, day - timedelta(days=3)):
        if article["published_date"] <= day.isoformat():
            articles.pop(title_key(article), None)  # Link a URL-less historical copy.
            articles[article["url"]] = dict(article, date=article["published_date"])
    unique = {}
    for article in articles.values():
        if day - timedelta(days=3) <= date.fromisoformat(article["date"]) <= day:
            unique[article.get("url") or title_key(article)] = article
    today = [a for a in unique.values() if a["date"] == day.isoformat()]
    past = [a for a in unique.values() if a["date"] < day.isoformat()]
    return today, past


def fda_context(day: date, snapshot: dict, saved: dict) -> tuple[str | None, list[dict], list[dict], int]:
    groups = {}
    for row in saved.values():
        for record in row["records"]:
            if record["date"] <= day.isoformat():
                identity = record.get("record_id") or json.dumps([
                    record.get(k) for k in ("package_ndc", "supplier", "generic_name", "record_kind")])
                groups.setdefault(record["date"], {})[identity] = record
    # A complete current-source date group replaces stale archived representations
    # in the new request only. The old stored request is untouched.
    fresh_groups = {}
    for record in snapshot["fda_records"]:
        if record["date"] <= day.isoformat():
            fresh_groups.setdefault(record["date"], []).append(record)
    groups.update({when: {str(i): r for i, r in enumerate(rows)} for when, rows in fresh_groups.items()})
    latest = max(groups, default=None)
    records = list(groups[latest].values()) if latest else []
    previous = max((when for when in groups if when < day.isoformat()), default=None)
    model_records = list(groups.get(day.isoformat(), {}).values())
    if previous:
        model_records += list(groups[previous].values())
    prior_date = max((row["record_date"] for row in saved.values()
                      if row["date"] < day.isoformat() and row.get("record_date")), default=None)
    newly_seen = len(records) if latest and (not prior_date or latest > prior_date) else 0
    return latest, records, model_records, newly_seen
