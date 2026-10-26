"""Check the deployed environment without writing a daily prediction."""
from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from backend.daily import fda_for, news_for
from model.prototype2.predict import predict


def main() -> None:
    day = datetime.now(ZoneInfo("America/Chicago")).date()
    news = news_for(day)
    fda_date, records = fda_for(day)
    result = predict({
        "target_date": day.isoformat(),
        "target_news": [{"title": "Influenza surveillance", "body": "Public health officials are tracking influenza reports."}],
        "past_news": [],
        "fda_recordings": [],
    }, device="cpu")
    assert len(result["ratings"]) == 28
    assert all(isinstance(value, int) and 1 <= value <= 10
               for value in result["ratings"].values())
    print({"day": day.isoformat(), "news_articles": len(news),
           "fda_record_date": fda_date, "fda_records": len(records),
           "model_ratings": len(result["ratings"])})


if __name__ == "__main__":
    main()
