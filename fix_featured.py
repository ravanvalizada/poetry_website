import os, psycopg2, psycopg2.extras

conn = psycopg2.connect(
    host=os.environ.get("DB_HOST","localhost"),
    port=os.environ.get("DB_PORT",5432),
    dbname=os.environ.get("DB_NAME","poetry_db"),
    user=os.environ.get("DB_USER","postgres"),
    password=os.environ.get("DB_PASSWORD","")
)
cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

# Remove featured flag from any badge_id that no longer exists in badges table
cur.execute("""
    UPDATE user_badges SET is_featured=0
    WHERE is_featured=1
    AND badge_id NOT IN (SELECT id FROM badges)
""")
print(f"Fixed {cur.rowcount} stale featured badge(s)")
conn.commit()
cur.close()
conn.close()
