from website.backend.signal_store import connect
db = connect()
rows = db.execute("""
    WITH ranked AS (
        SELECT r.signal_uid, r.observation_date, r.forecast_horizon, d.signal_id,
               ROW_NUMBER() OVER (PARTITION BY r.signal_uid ORDER BY r.ingested_at DESC) as rn
        FROM signal_records r
        JOIN signal_definitions d ON d.id = r.signal_uid
    )
    SELECT * FROM ranked WHERE rn = 1
""").fetchall()
from datetime import datetime, timezone, timedelta
now = datetime.now(timezone.utc)
recent_threshold = now - timedelta(days=90)
c = 0
for row in rows:
    obs = row["observation_date"]
    recent = bool(obs and datetime.fromisoformat(obs).astimezone(timezone.utc) >= recent_threshold)
    hor = row["forecast_horizon"]
    curr = bool(hor and hor >= str(now.year))
    if not (recent or curr):
        print(row["signal_id"], obs, hor)
        c += 1
print(f"Total: {c}")
