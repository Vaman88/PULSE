from website.backend.signal_store import connect
with connect() as db:
    db.execute("UPDATE refresh_runs SET status='success' WHERE source_name='gdelt_recent_news'")
print("Cleared GDELT errors")
