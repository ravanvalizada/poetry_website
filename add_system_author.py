import sqlite3
from datetime import datetime

DB_PATH = "database.db"

conn = sqlite3.connect(DB_PATH)
c = conn.cursor()

# Add new system author
username = "Yusuf Hayaloğlu"
profile_picture = "default_user.jpg"  # or provide a URL/path if you have one
is_system_author = 1  # mark as system author

c.execute("""
INSERT INTO users (username, profile_picture, is_admin, is_system_author, can_login)
VALUES (?, ?, ?, ?, ?)
""", (username, profile_picture, 0, is_system_author, 0))

conn.commit()

# get the new author's ID
new_author_id = c.lastrowid
print(f"New system author added: {username} with user_id={new_author_id}")

conn.close()