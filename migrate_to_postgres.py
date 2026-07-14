"""
migrate_to_postgres.py
----------------------
Reads every table from database.db (SQLite) and inserts all rows
into the PostgreSQL database configured via environment variables.

Usage:
    DB_HOST=localhost DB_PORT=5432 DB_NAME=poetry_db \
    DB_USER=postgres DB_PASSWORD=secret \
    python migrate_to_postgres.py

Requirements:
    pip install psycopg2-binary
"""

import os
import sqlite3
import psycopg2
import psycopg2.extras

# ── Config ────────────────────────────────────────────────────────────────────
SQLITE_FILE = "database.db"

PG_CONFIG = dict(
    host     = os.environ.get("DB_HOST",     "localhost"),
    port     = os.environ.get("DB_PORT",     5432),
    dbname   = os.environ.get("DB_NAME",     "poetry_db"),
    user     = os.environ.get("DB_USER",     "postgres"),
    password = os.environ.get("DB_PASSWORD", ""),
)

# Tables to migrate in dependency order (parents before children)
# sqlite_sequence is internal to SQLite — skip it
TABLES = [
    "users",
    "authors",
    "badges",
    "poems",
    "comments",
    "poem_approvals",
    "notifications",
    "followers",
    "conversations",
    "messages",
    "quotes",
    "quote_likes",
    "saved_poems",
    "saved_quotes",
    "collections",
    "collection_poems",
    "collection_follows",
    "chain_poems",
    "chain_stanzas",
    "weekly_themes",
    "weekly_theme_poems",
    "duels",
    "duel_votes",
    "daily_game_questions",
    "daily_game_attempts",
    "daily_game_streaks",
    "salon_circles",
    "circle_members",
    "circle_messages",
    "circle_posts",
    "living_poem_lines",
    "living_poem_queue",
    "poem_recitations",
    "recitation_likes",
    "recitation_comments",
    "line_comments",
    "poem_views_log",
    "user_badges",
    "profile_picture_history",
    "username_history",
    "collaborative_poems",
    "collaborative_stanzas",
]


def get_sqlite_tables(sqlite_conn):
    """Return set of table names that actually exist in the SQLite DB."""
    rows = sqlite_conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ).fetchall()
    return {r[0] for r in rows}


def get_pg_tables(pg_cur):
    """Return set of table names that exist in the PostgreSQL DB."""
    pg_cur.execute("""
        SELECT table_name FROM information_schema.tables
        WHERE table_schema = 'public'
    """)
    return {r["table_name"] for r in pg_cur.fetchall()}


def migrate_table(table, sqlite_conn, pg_conn, pg_cur):
    rows = sqlite_conn.execute(f'SELECT * FROM "{table}"').fetchall()
    if not rows:
        print(f"  {table}: 0 rows — skipped")
        return 0

    # Column names from SQLite cursor description
    cols = [d[0] for d in sqlite_conn.execute(
        f'SELECT * FROM "{table}" LIMIT 0'
    ).description]

    # Build INSERT ... ON CONFLICT DO NOTHING
    col_list   = ", ".join(f'"{c}"' for c in cols)
    placeholders = ", ".join(["%s"] * len(cols))
    sql = f'INSERT INTO "{table}" ({col_list}) VALUES ({placeholders}) ON CONFLICT DO NOTHING'

    # Convert rows to plain tuples
    data = [tuple(r) for r in rows]

    try:
        psycopg2.extras.execute_batch(pg_cur, sql, data, page_size=500)
        pg_conn.commit()
        print(f"  {table}: {len(data)} rows ✅")
        return len(data)
    except Exception as e:
        pg_conn.rollback()
        print(f"  {table}: ❌  {e}")
        return 0


def fix_sequences(pg_conn, pg_cur, tables):
    """Reset PostgreSQL SERIAL sequences to max(id) so new inserts don't collide."""
    for table in tables:
        try:
            pg_cur.execute(f"""
                SELECT setval(
                    pg_get_serial_sequence('"{table}"', 'id'),
                    COALESCE((SELECT MAX(id) FROM "{table}"), 1)
                )
            """)
            pg_conn.commit()
        except Exception:
            pg_conn.rollback()  # Table has no serial id column — fine


def main():
    print("Connecting to SQLite …")
    sqlite_conn = sqlite3.connect(SQLITE_FILE)
    sqlite_conn.row_factory = sqlite3.Row

    print("Connecting to PostgreSQL …")
    pg_conn = psycopg2.connect(**PG_CONFIG)
    pg_conn.autocommit = False
    pg_cur  = pg_conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    existing_sqlite = get_sqlite_tables(sqlite_conn)
    existing_pg     = get_pg_tables(pg_cur)

    total_rows = 0
    print("\nDisabling foreign key checks for migration …")
    pg_cur.execute("SET session_replication_role = 'replica';")
    pg_conn.commit()

    print("Migrating tables …")
    for table in TABLES:
        if table not in existing_sqlite:
            print(f"  {table}: not in SQLite — skipped")
            continue
        if table not in existing_pg:
            print(f"  {table}: not in PostgreSQL — skipped (run app once first to create schema)")
            continue
        total_rows += migrate_table(table, sqlite_conn, pg_conn, pg_cur)

    print(f"\nRe-enabling foreign key checks …")
    pg_cur.execute("SET session_replication_role = 'origin';")
    pg_conn.commit()

    print(f"\nFixing SERIAL sequences …")
    fix_sequences(pg_conn, pg_cur, [t for t in TABLES if t in existing_pg])

    print(f"\n✅ Migration complete — {total_rows} total rows inserted.")

    pg_cur.close()
    pg_conn.close()
    sqlite_conn.close()


if __name__ == "__main__":
    main()
