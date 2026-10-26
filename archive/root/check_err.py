from website.backend.signal_store import connect
db = connect()
res = db.execute("SELECT error FROM refresh_runs WHERE source_name='gdelt_recent_news' ORDER BY started_at DESC LIMIT 1").fetchone()
print(res[0] if res else "No error found")
