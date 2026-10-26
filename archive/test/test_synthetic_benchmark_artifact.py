"""Integrity checks for the transparent synthetic pharmacy benchmark.

These checks deliberately do not assert that the model wins. They protect the
artifact's reproducibility, accounting invariants, and disclosure boundary.
"""

import csv
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data" / "synthetic_pharmacy_data"
CSV_PATH = DATA / "arkansas_clinic_daily_pharmacy_sales.csv"


def test_sales_artifact_shape_and_scope():
    with CSV_PATH.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        rows = list(reader)

    assert len(rows) == 30 * 1096
    assert reader.fieldnames == [
        "date", "state", "facility_type", "drug_name",
        "therapeutic_class", "units_sold", "unit_price_usd",
        "revenue_usd", "stockout_flag", "injected_event_tags",
    ]
    assert {row["state"] for row in rows} == {"Arkansas"}
    assert {row["facility_type"] for row in rows} == {"local government clinic"}
    assert len({row["drug_name"] for row in rows}) == 30
    assert len({row["date"] for row in rows}) == 1096
    assert rows[0]["date"] == "2023-01-01"
    assert rows[-1]["date"] == "2025-12-31"


def test_sales_accounting_and_event_tags():
    with CSV_PATH.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))

    tags = set()
    for row in rows:
        units = int(row["units_sold"])
        price = float(row["unit_price_usd"])
        revenue = float(row["revenue_usd"])
        assert units >= 0
        assert price > 0
        assert revenue >= 0
        assert round(units * price, 2) == round(revenue, 2)
        tags.update(filter(None, row["injected_event_tags"].split("|")))

    assert {"winter_respiratory", "flu_2023_24", "covid_waves"} <= tags


def test_disclosure_is_present():
    guide = (DATA / "synthetic_guide.md").read_text(encoding="utf-8")
    protocol = (DATA / "benchmark_protocol.md").read_text(encoding="utf-8")
    assert "entirely synthetic" in guide
    assert "not a transaction extract" in protocol
    assert "single-site" in protocol
