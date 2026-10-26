"""Check that the live site renders its API backed pages in a browser."""
from __future__ import annotations

from pathlib import Path

from playwright.sync_api import sync_playwright

SCREENSHOT = Path("/tmp/pulse-live-signals.png")


def main() -> None:
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 900})
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.goto("https://pulseintelligence.tech/", wait_until="networkidle")
        assert page.title() == "PULSE — public health signals"
        page.get_by_role("button", name="Daily signals", exact=True).click()
        page.locator(".signal-row").first.wait_for(timeout=15000)
        assert page.locator(".signal-row").count() == 28
        assert "2026-10-23" in page.locator("#signals-content").inner_text()
        page.screenshot(path=str(SCREENSHOT), full_page=True)
        page.get_by_role("button", name="API docs").click()
        page.get_by_text("Scoring contract").wait_for(timeout=15000)
        page.get_by_role("button", name="Pharmacy portal").click()
        page.locator("#pharmacy-auth").wait_for(timeout=15000)
        assert not errors, errors
        print({"title": page.title(), "signal_rows": 28,
               "api_docs": "rendered", "pharmacy_sign_in": "rendered",
               "javascript_errors": 0, "screenshot": str(SCREENSHOT)})
        browser.close()


if __name__ == "__main__":
    main()
