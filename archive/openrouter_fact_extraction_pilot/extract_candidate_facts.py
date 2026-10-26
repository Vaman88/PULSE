"""Extract only quoted facts from indexed articles with OpenRouter DeepSeek.

This creates triage facts, never final output scores. Run in small resumable batches.
"""
from __future__ import annotations

import argparse
import gzip
import json
import os
import sqlite3
import time
import urllib.error
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
DB = HERE / "review_index.sqlite"
OUT = HERE / "candidate_facts.jsonl"
API = "https://openrouter.ai/api/v1/chat/completions"
MODEL = "deepseek/deepseek-v4.1-flash"

INSTRUCTIONS = """You extract literal facts from dated news excerpts for later human review.
Use ONLY the supplied article title and excerpt. No outside knowledge, diagnosis,
prediction, risk score, or inferred connection. The candidate disease term may be a
false match. A past outbreak mentioned in an article is historical, not current.
Arkansas means the US state; a vessel or organization named Arkansas is not the state.
Return JSON with an 'items' array, one item per input ID, in the same order.
For each: id (integer); relevant (boolean: the candidate disease is actually discussed);
kind (one of 'current_outbreak', 'current_cases', 'suspected_cases', 'symptoms_only',
'historical', 'general_information', 'other'); location_quote (exact excerpt substring
supporting location, or empty string); time_quote (exact excerpt substring supporting
event timing, or empty string); support_quote (exact nonempty excerpt substring
supporting kind when relevant, else empty string); confirmed (boolean only if the
excerpt directly says the disease/pathogen was confirmed); cases (integer only if
the excerpt gives an explicit count for this disease, else null); deaths (same rule);
arkansas_state (boolean only if the excerpt clearly identifies the US state as
the location of cases/symptoms). Do not infer any missing field. Return JSON only."""


def request_facts(items: list[dict], key: str) -> dict:
    prompt_items = [{"id": item["id"], "candidate_signal": item["signal"],
                     "article_date": item["date"], "title": item["title"],
                     "excerpt": item["excerpt"]} for item in items]
    body = json.dumps({
        "model": MODEL,
        "temperature": 0,
        "max_tokens": 4000,
        "reasoning": {"enabled": False},
        "response_format": {"type": "json_object"},
        "messages": [
            {"role": "system", "content": INSTRUCTIONS},
            {"role": "user", "content": json.dumps(prompt_items, ensure_ascii=False)},
        ],
    }, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(API, data=body,
                                 headers={"Authorization": "Bearer " + key,
                                          "Content-Type": "application/json",
                                          "HTTP-Referer": "https://openrouter.ai/"}, method="POST")
    for attempt in range(4):
        try:
            with urllib.request.urlopen(req, timeout=120) as response:
                return json.load(response)
        except urllib.error.HTTPError as exc:
            if exc.code not in (429, 500, 502, 503, 504) or attempt == 3:
                raise
            time.sleep(2 ** attempt)
    raise RuntimeError("unreachable")


def validate(parsed: dict, items: list[dict]) -> list[dict]:
    values = parsed.get("items")
    assert isinstance(values, list) and len(values) == len(items)
    assert [x.get("id") for x in values] == [x["id"] for x in items]
    allowed_kinds = {"current_outbreak", "current_cases", "suspected_cases",
                     "symptoms_only", "historical", "general_information", "other"}
    for value, source in zip(values, items):
        assert value["kind"] in allowed_kinds
        assert type(value["relevant"]) is bool
        assert type(value["confirmed"]) is bool
        assert type(value["arkansas_state"]) is bool
        full = (source["title"] or "") + "\n" + source["excerpt"]
        for field in ("support_quote", "location_quote", "time_quote"):
            assert isinstance(value[field], str) and value[field] in full, (value["id"], field)
        if value["relevant"]:
            assert value["support_quote"], value["id"]
        for field in ("cases", "deaths"):
            assert value[field] is None or (type(value[field]) is int and value[field] >= 0)
    return values


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--batch-size", type=int, default=5)
    parser.add_argument("--limit-batches", type=int, default=1,
                        help="Use 0 for every remaining batch")
    args = parser.parse_args()
    assert 1 <= args.batch_size <= 10
    key = os.environ.get("OPENROUTER_API_KEY")
    if not key:
        raise SystemExit("OPENROUTER_API_KEY is missing")
    completed = set()
    if OUT.exists():
        for line in OUT.read_text(encoding="utf-8").splitlines():
            record = json.loads(line)
            completed.update(record["source_ids"])
    export_path = HERE / "candidate_rows.jsonl.gz"
    if export_path.exists():
        with gzip.open(export_path, "rt", encoding="utf-8") as stream:
            rows = [json.loads(line) for line in stream]
    else:
        db = sqlite3.connect(DB)
        rows = [dict(zip(("id", "date", "signal", "url", "title", "excerpt"), row))
                for row in db.execute("""SELECT rowid,date,signal,url,title,excerpt FROM candidates
                ORDER BY rowid""")]
    remaining = [row for row in rows if row["id"] not in completed]
    print(json.dumps({"candidate_rows": len(rows), "already_extracted": len(completed),
                      "remaining": len(remaining)}), flush=True)
    batches = [remaining[i:i + args.batch_size] for i in range(0, len(remaining), args.batch_size)]
    for number, items in enumerate(batches):
        if args.limit_batches and number >= args.limit_batches:
            break
        response = request_facts(items, key)
        choice = response["choices"][0]
        raw_content = choice["message"].get("content")
        if not raw_content:
            raise RuntimeError(f"Model returned no content: finish_reason={choice.get('finish_reason')}, usage={response.get('usage')}")
        facts = validate(json.loads(raw_content), items)
        record = {"model": response.get("model", MODEL), "request_id": response.get("id"),
                  "source_ids": [x["id"] for x in items], "source_urls": [x["url"] for x in items],
                  "usage": response.get("usage"), "facts": facts}
        with OUT.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(record, ensure_ascii=False) + "\n")
        print(json.dumps({"batch": number + 1, "items": len(items),
                          "usage": response.get("usage")}), flush=True)


if __name__ == "__main__":
    main()
