"""Exercise public API and private account flow through the live HTTPS domain."""
from __future__ import annotations

import json
import secrets
import subprocess
import time
from http.cookiejar import CookieJar
from urllib.error import HTTPError
from urllib.request import HTTPCookieProcessor, Request, build_opener

BASE = "https://pulseintelligence.tech/api/v1"


def main() -> None:
    client = build_opener(HTTPCookieProcessor(CookieJar()))

    def call(method: str, path: str, body: bytes | None = None, content_type: str = "application/json"):
        request = Request(BASE + path, data=body, method=method,
                          headers={"Content-Type": content_type})
        with client.open(request, timeout=60) as response:
            return response.status, json.load(response), response.headers

    latest = call("GET", "/signals/latest")[1]
    assert len(latest["outputs"]) == 28
    freshness = call("GET", "/signals/freshness")[1]
    assert freshness["latest_prediction_date"] == latest["date"]
    history = call("GET", "/signals/history?start=2026-08-01&end=" + latest["date"])[1]
    assert history["count"] >= 20
    call("GET", "/news/relevant")

    try:
        call("GET", "/plan")
        raise AssertionError("Unauthenticated plan access succeeded")
    except HTTPError as error:
        assert error.code == 401

    username = "deploycheck" + secrets.token_hex(5)
    password = secrets.token_urlsafe(24)
    credentials = json.dumps({"username": username, "password": password}).encode()
    _, account, headers = call("POST", "/auth/signup", credentials)
    assert account["username"] == username
    cookie = headers["Set-Cookie"].lower()
    assert "secure" in cookie and "httponly" in cookie and "samesite=strict" in cookie
    assert call("GET", "/plan")[1] == {"recommendations": []}

    dates = sorted({row["date"] for row in history["records"]})[:40]
    csv = "date,drug_name,unit,starting_stock,quantity_sold,ending_stock,current_stock,expected_delivery\n"
    csv += "\n".join(f"{day},Deployment test tablets,tablets,500,{10 + i % 5},{490 - i % 5},100,5"
                     for i, day in enumerate(dates))
    plan = call("POST", "/plan", csv.encode(), "text/csv")[1]
    assert len(plan["recommendations"]) == 1
    assert len(plan["recommendations"][0]["daily_forecasts"]) == 14
    assert call("GET", "/plan")[1] == plan
    assert call("POST", "/logout", b"{}")[1] == {"ok": True}
    try:
        call("GET", "/plan")
        raise AssertionError("Logged-out plan access succeeded")
    except HTTPError as error:
        assert error.code == 401
    subprocess.run(["ssh", "dev_server", "sudo", "systemctl", "restart", "pulse-api.service"], check=True)
    for _ in range(30):
        try:
            call("GET", "/signals/latest")
            break
        except Exception:
            time.sleep(1)
    else:
        raise RuntimeError("API did not return after a service restart")
    assert call("POST", "/auth/login", credentials)[1]["username"] == username
    assert call("GET", "/plan")[1] == plan
    call("POST", "/logout", b"{}")
    subprocess.run(["ssh", "dev_server", "/opt/pulse-venv/bin/python",
                    "/opt/pulse/deploy/oracle/cleanup_test_account.py", username], check=True)
    print(json.dumps({"latest_prediction_date": latest["date"], "history_records": history["count"],
                      "signup_login_logout": "passed", "plan_upload_and_restart_persistence": "passed",
                      "test_username": username}))


if __name__ == "__main__":
    main()
