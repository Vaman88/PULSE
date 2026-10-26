"""Focused proof-of-concept checks for pharmacy upload, model and account privacy."""
from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient

from backend import api, demand
from model.prototype2.model import TARGETS


def make_signals(n=45):
    base = date(2026, 8, 1)
    return [{"date": (base + timedelta(days=i)).isoformat(),
             "outputs": {name: (i * (j + 1) % 10) + 1 for j, name in enumerate(TARGETS)}}
            for i in range(n)]


def make_csv(n=40, cap_every=0):
    base = date(2026, 8, 1)
    lines = ["date,drug_name,unit,starting_stock,quantity_sold,ending_stock,current_stock,expected_delivery"]
    for i in range(n):
        sold = 10 + (i % 5)
        stock = sold if cap_every and i % cap_every == 0 else 500
        end = 0 if stock == sold else stock - sold
        lines.append(f"{base + timedelta(days=i)},Example tablets,tablets,{stock},{sold},{end},100,5")
    return "\n".join(lines)


def test_csv_rejects_missing_duplicate_negative_units_and_overstock():
    with pytest.raises(ValueError, match="must contain"):
        demand.validate_csv("date,drug_name\n2026-01-01,A")
    with pytest.raises(ValueError, match="duplicate date"):
        demand.validate_csv("date,drug_name,unit,starting_stock,quantity_sold\n2026-01-01,A,box,5,1\n2026-01-01,A,box,5,1")
    with pytest.raises(ValueError, match="nonnegative"):
        demand.validate_csv("date,drug_name,unit,starting_stock,quantity_sold\n2026-01-01,A,box,-1,0")
    with pytest.raises(ValueError, match="unit must be consistent"):
        demand.validate_csv("date,drug_name,unit,starting_stock,quantity_sold\n2026-01-01,A,box,5,1\n2026-01-02,A,each,5,1")
    with pytest.raises(ValueError, match="exceeds"):
        demand.validate_csv("date,drug_name,unit,starting_stock,quantity_sold\n2026-01-01,A,box,1,2")


def test_chronological_validation_reports_baseline_comparison_and_original_units():
    result = demand.forecast(make_csv(), make_signals())[0]
    assert result["unit"] == "tablets"
    assert result["matched_days"] == 40
    assert result["uncertainty"]["validation_days"] > 0
    assert result["uncertainty"]["method"] in {"ridge", "historical_baseline"}
    assert len(result["selected_signals"]) == 3
    assert all(1 <= s["current_score"] <= 10 for s in result["selected_signals"])
    assert result["buy_7d"] is not None and result["demand_14d"] >= result["demand_7d"]
    assert len(result['history']) == 30
    assert result['history'][0] == {'date':'2026-08-11', 'stock':490.0, 'sales':10.0}
    assert result['history'][-1] == {'date':'2026-09-09', 'stock':486.0, 'sales':14.0}


def test_stockout_censoring_is_reported_and_bounded():
    result = demand.forecast(make_csv(cap_every=5), make_signals())[0]
    assert result["uncertainty"]["stock_constrained_days"] == 8
    assert result["uncertainty"]["reserve_uplift_pct"] == 2
    high_rate = demand.forecast(make_csv(cap_every=3), make_signals())[0]
    assert high_rate["uncertainty"]["reserve_uplift_pct"] == 5


def test_missing_current_stock_makes_purchase_quantity_conditional():
    source = "\n".join(",".join(line.split(",")[:5]) for line in make_csv().splitlines())
    result = demand.forecast(source, make_signals())[0]
    assert result["buy_7d"] is None and result["buy_14d"] is None
    assert result["stock_status"].startswith("conditional_estimate")
    assert result['history'][-1]['stock'] == 486


def test_chart_history_is_last_30_calendar_days_without_filling_missing_dates():
    lines = make_csv(70).splitlines()
    result = demand.forecast('\n'.join([lines[0]] + lines[1:][::2]), make_signals(75))[0]
    assert len(result['history']) == 15
    assert result['history'][0]['date'] == '2026-09-10'
    assert result['history'][-1]['date'] == '2026-10-08'
    assert set(result['history'][0]) == {'date', 'stock', 'sales'}


def test_auth_isolation_logout_and_upload_retention(monkeypatch, tmp_path):
    accounts, plans = tmp_path / "accounts.json", tmp_path / "plans.json"
    monkeypatch.setattr(api, "ACCOUNTS", accounts)
    monkeypatch.setattr(api, "PLANS", plans)
    monkeypatch.setattr(api, "series", make_signals)
    client = TestClient(api.app)
    assert client.get("/api/v1/plan").status_code == 401
    password = "example-password-long"
    for username in ("alice", "bob"):
        response = client.post("/api/v1/auth/signup", json={"username": username, "password": password})
        assert response.status_code == 200
        assert "httponly" in response.headers["set-cookie"].lower()
        if username == "alice":
            uploaded = client.post("/api/v1/plan", content=make_csv(), headers={"Content-Type": "text/csv"})
            assert uploaded.status_code == 200
            alice_plan = uploaded.json()
            assert client.post("/api/v1/logout").status_code == 200
            assert client.post("/api/v1/auth/login", json={"username": username, "password": password}).status_code == 200
            assert client.get("/api/v1/plan").json()["recommendations"]
            assert client.post("/api/v1/logout").status_code == 200
        else:
            assert client.get("/api/v1/plan").json() == {"recommendations": []}
    saved_accounts, saved_plans = accounts.read_text(), plans.read_text()
    assert password not in saved_accounts
    assert "quantity_sold" not in saved_plans and "starting_stock" not in saved_plans
    assert "Example tablets" in saved_plans
    assert len(alice_plan['recommendations'][0]['history']) == 30
    assert client.get('/api/v1/plan').json() == {'recommendations': []}
    assert {p.name for p in tmp_path.iterdir()} == {"accounts.json", "plans.json"}
    assert len(alice_plan["recommendations"][0]["daily_forecasts"]) == 14
    assert client.post("/api/v1/logout").status_code == 200
    assert alice_plan["recommendations"][0]["daily_demand"] > 0

