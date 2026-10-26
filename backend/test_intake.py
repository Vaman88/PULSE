"""Regression checks for real intake failures and non-destructive recovery."""
import json
from datetime import date
from urllib.parse import parse_qs, urlparse

import pytest
from fastapi.testclient import TestClient

from backend import api, daily, intake, runner
from model.prototype2.model import TARGETS

STAMP = "2026-10-25T17:00:00+00:00"


def fda_record(day="10/23/2026", **extra):
    return {"update_date": day, "initial_posting_date": "03/10/2023",
            "generic_name": "Clonazepam", "package_ndc": "123", "status": "Current", **extra}


def test_fda_paginates_missing_change_dates_and_parses_across_years(monkeypatch):
    pages = [fda_record("12/31/2025", package_ndc=str(i)) for i in range(1000)]
    calls = []

    def fetch(url):
        query = parse_qs(urlparse(url).query)
        offset = int(query["skip"][0]); calls.append(offset)
        return json.dumps({"meta": {"last_updated": "2026-10-23", "results": {"total": 1002}},
                           "results": pages if offset == 0 else [fda_record(), fda_record("10/26/2026")]}).encode()

    monkeypatch.setattr(intake, "fetch", fetch)
    records, raw, meta = intake.fetch_fda(date(2026, 10, 25), STAMP)
    assert calls == [0, 1000] and len(raw) == meta["total"] == 1002
    newest = max(records, key=lambda r: r["date"])
    assert newest["date"] == "2026-10-23"
    assert newest["source_date_field"] == "update_date" and newest["status"] == "Current"
    assert newest["collected_at"] == STAMP and newest["recovered"]
    assert all(r["date"] <= "2026-10-25" for r in records)


def test_fda_falls_back_to_initial_posting_and_rejects_incomplete_pages(monkeypatch):
    raw = fda_record(); raw.pop("update_date")
    monkeypatch.setattr(intake, "fetch", lambda _: json.dumps({"meta": {"results": {"total": 1}}, "results": [raw]}).encode())
    assert intake.fetch_fda(date(2026, 10, 25), STAMP)[0][0]["date"] == "2023-03-10"
    monkeypatch.setattr(intake, "fetch", lambda _: b'{"meta":{"results":{"total":1}},"results":[]}')
    with pytest.raises(RuntimeError, match="Incomplete"):
        intake.fetch_fda(date(2026, 10, 25), STAMP)


def test_news_lookback_source_dates_and_empty_feed_failure(monkeypatch):
    rss = b'''<rss><channel><item><title>Influenza report</title><link>https://example.org/a</link>
    <pubDate>Fri, 23 Oct 2026 01:00:00 +0000</pubDate></item></channel></rss>'''
    def fetch(url):
        if url.endswith("rss-feeds"):
            return b'<a href="/flu/rss">Influenza</a>'
        if url.endswith("/rss"):
            return rss
        return ('<div class="field--name-field-body">' + 'Full article. ' * 20 + '</div>').encode()
    monkeypatch.setattr(intake, "fetch", fetch)
    news, meta = intake.fetch_news(date(2026, 10, 22), date(2026, 10, 25), STAMP)
    assert len(news) == 1 and news[0]["published_date"] == "2026-10-22"
    assert news[0]["recovered"] and news[0]["collected_at"] == STAMP
    assert meta["feeds_checked"] == 1
    rss = b'<rss><channel/></rss>'
    with pytest.raises(RuntimeError, match="Empty or invalid"):
        intake.fetch_news(date(2026, 10, 22), date(2026, 10, 25), STAMP)


def snapshot(store, news, fda=None):
    folder = store / "collections"; folder.mkdir(exist_ok=True)
    value = {"target_date": "2026-10-25", "collected_at": STAMP, "news": news,
             "fda_records": fda or [], "fda_fetch": {"last_updated": "2026-10-23"}}
    (folder / "20261025.json").write_text(json.dumps(value))
    return value


def article(**extra):
    return {"title": "Recovered report", "body": "background " * 100 + " influenza",
            "url": "https://example.org/flu", "published_date": "2026-10-22",
            "collected_at": STAMP, "recovered": True, "date_basis": "cidrap_rss_pubDate_America/Chicago", **extra}


def test_recovered_news_deduplicates_archived_title_and_uses_corrected_date(tmp_path):
    snapshot(tmp_path, [article()])
    saved = {"1": {"date": "2026-10-21", "news": [{"title": "Recovered report", "body": "old body"}]}}
    today, past = intake.news_context(tmp_path, date(2026, 10, 25), saved)
    assert not today and len(past) == 1
    assert past[0]["date"] == "2026-10-22" and past[0]["recovered"]
    assert saved["1"]["date"] == "2026-10-21"


def test_collection_reader_bounds_current_window_but_keeps_archives(tmp_path):
    snapshot(tmp_path, [article()])
    old = tmp_path / "collections/20261001.json"
    old.write_text(json.dumps({"news": [article(url="https://example.org/old", published_date="2026-10-01")]}))
    assert len(intake.collected_news(tmp_path)) == 1
    assert len(intake.collected_news(tmp_path, date(2026, 10, 1))) == 2
    assert old.exists()


def test_public_news_shows_recovered_full_body_match_with_provenance(monkeypatch, tmp_path):
    snapshot(tmp_path, [article()])
    monkeypatch.setattr(api, "DATA", tmp_path)
    monkeypatch.setattr(api, "latest", lambda: {"date": "2026-10-24", "executed_at": STAMP,
                                                "outputs": {"influenza_1w": 4}})
    monkeypatch.setattr(api, "news_provenance", lambda: {})
    monkeypatch.setattr(api, "read_kind", lambda _: {"old": {"date": "2026-10-21", "inputs": {"news": [article(published_date="2026-10-21")]}}})
    rows = TestClient(api.app).get('/api/v1/news/relevant').json()["articles"]
    assert len(rows) == 1 and rows[0]["date"] == "2026-10-22"
    assert rows[0]["recovered"] and rows[0]["collected_at"] == STAMP
    assert rows[0]["diseases"] == ["influenza"]


def test_next_run_consumes_recovered_inputs_without_revising_old_predictions(monkeypatch, tmp_path):
    from model.prototype2 import predict
    monkeypatch.setattr(daily, "STORE", tmp_path)
    monkeypatch.setattr(runner, "STORE", tmp_path)
    old_output = {"date": "2026-10-24", "model": "prototype2", "outputs": {n: 1 for n in TARGETS}}
    old_fda = {"date": "2026-10-24", "record_date": "2026-09-30",
               "records": [{"date": "2026-09-30", "generic_name": "old"}]}
    for kind, rows in (("output", {"1": old_output}), ("fda", {"1": old_fda}),
                       ("news", {"1": {"date": "2026-10-24", "news": []}}),
                       ("input", {"1": {"date": "2026-10-24", "inputs": {"news": []}}})):
        runner.write_kind(kind, rows)
    (tmp_path / "metadata.json").write_text(json.dumps({"latest_prediction_date": "2026-10-24"}))
    recovered_fda = {"date": "2026-10-23", "generic_name": "Clonazepam", "status": "Current"}
    current = snapshot(tmp_path, [article()], [recovered_fda])
    monkeypatch.setattr(intake, "collect", lambda *args: current)
    def inference(request):
        assert request["fda_recordings"] == [recovered_fda]
        assert request["past_news"][0]["date"] == "2026-10-22"
        return {"ratings": {n: 2 for n in TARGETS}, "disease_frequency": {}, "article_diseases": []}
    monkeypatch.setattr(predict, "predict", inference)
    daily.run(date(2026, 10, 25))
    assert runner.read_kind("output")["1"] == old_output
    current_fda = next(r for r in runner.read_kind("fda").values() if r["date"] == "2026-10-25")
    assert current_fda["record_date"] == "2026-10-23" and current_fda["new_records"] == 1
    prior = {"2": dict(current_fda, date="2026-10-25")}
    assert intake.fda_context(date(2026, 10, 26), current, prior)[3] == 0
    metadata = json.loads((tmp_path / "metadata.json").read_text())
    assert metadata["latest_news_date"] == "2026-10-22"
