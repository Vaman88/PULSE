"""Run with pytest backend/test_frontend.py; uses the checked-in runtime snapshot."""
from pathlib import Path
import re

from fastapi.testclient import TestClient
from backend.api import app
from backend import api


def test_frontend_assets_and_live_contracts():
    client = TestClient(app)
    html = client.get('/').text
    nav = re.search(r'<nav class="nav".*?</nav>', html, re.S)[0]
    assert re.findall(r'<button[^>]*>([^<]+)</button>', nav) == ['Home', 'Workspace', 'Signals', 'API', 'Sign in']
    for path in ['/live.js', '/public/style.css', '/public/pulse-supply-mark.svg', '/public/sales-template.csv', '/docs', '/openapi.json']:
        assert client.get(path).status_code == 200, path
    for path in (Path(__file__).resolve().parents[1] / 'frontend').rglob('*'):
        if path.suffix in {'.html', '.css', '.js'}:
            text = path.read_text(encoding='utf-8')
            assert 'website_target' not in text, path
            assert '\ufffd' not in text and '\u0081' not in text, path
    scores = client.get('/api/v1/signals/latest').json()['outputs']
    assert len(scores) == 28 and all(type(value) is int and 1 <= value <= 10 for value in scores.values())
    assert client.get('/api/v1/plan').status_code == 401


def test_relevant_news_newest_first_without_four_article_limit(monkeypatch):
    monkeypatch.setattr(api, 'latest', lambda: {'date': '2026-10-25', 'executed_at': '2026-10-25T12:00:00+00:00', 'outputs': {'influenza_1w': 7}})
    monkeypatch.setattr(api, 'news_provenance', lambda: {})
    samples = {str(day): {'date': f'2026-10-{day}', 'inputs': {'news': [
        {'title': f'Influenza update {day}', 'body': 'influenza', 'url': f'https://example.org/{day}'}
    ]}} for day in range(20, 26)}
    samples['duplicate'] = samples['20']
    monkeypatch.setattr(api, 'read_kind', lambda kind: samples)
    rows = TestClient(app).get('/api/v1/news/relevant').json()['articles']
    assert [row['date'] for row in rows] == [f'2026-10-{day}' for day in range(25, 19, -1)]
    assert len({row['url'] for row in rows}) == 6
