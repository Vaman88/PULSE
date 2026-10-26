"""Remove one temporary account created by verify_live.py."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path


def main() -> None:
    username = sys.argv[1]
    if not username.startswith("deploycheck"):
        raise ValueError("Only deployment test accounts may be removed")
    data = Path(__file__).resolve().parents[2] / "backend" / "data"
    for name in ("accounts.json", "plans.json"):
        path = data / name
        if not path.exists():
            continue
        records = json.loads(path.read_text(encoding="utf-8"))
        records.pop(username, None)
        temporary = path.with_suffix(path.suffix + ".cleanup.tmp")
        temporary.write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
    print(f"Removed deployment test account {username}")


if __name__ == "__main__":
    main()
