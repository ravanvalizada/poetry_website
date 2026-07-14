import sqlite3

db = sqlite3.connect("database.db")

# ---------- QUOTES ----------
# ---------- QUOTE LIKES ----------
db.execute("""
ALTER TABLE messages ADD COLUMN iv TEXT;
""");

db.commit()
db.close()
