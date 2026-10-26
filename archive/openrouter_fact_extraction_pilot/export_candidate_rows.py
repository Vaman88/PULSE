"""Export the read-only SQLite candidate index for Windows-side API extraction."""
import gzip
import json
import sqlite3
from pathlib import Path

here = Path(__file__).resolve().parent
db = sqlite3.connect(here / "review_index.sqlite")
path = here / "candidate_rows.jsonl.gz"
with gzip.open(path, "wt", encoding="utf-8") as stream:
    for row in db.execute("SELECT rowid,date,signal,url,title,excerpt FROM candidates ORDER BY rowid"):
        stream.write(json.dumps(dict(zip(("id", "date", "signal", "url", "title", "excerpt"), row)), ensure_ascii=False) + "\n")
print(path)
