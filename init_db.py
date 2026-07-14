import sqlite3

db = sqlite3.connect("database.db")

# ---------- USERS ----------
db.execute("""
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    email TEXT UNIQUE,
    username TEXT UNIQUE,
    password TEXT,
    is_admin INTEGER DEFAULT 0,
    profile_picture TEXT DEFAULT 'default_avatar.jpg'
)
""")

# ---------- POEMS ----------
db.execute("""
CREATE TABLE IF NOT EXISTS poems (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT,
    text TEXT,
    genre TEXT,
    language TEXT,
    author TEXT,
    date TEXT,
    user_id INTEGER,
    status TEXT DEFAULT 'waiting'
)
""")

# ---------- COMMENTS ----------
db.execute("""
CREATE TABLE IF NOT EXISTS comments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    poem_id INTEGER,
    user_id INTEGER,
    parent_id INTEGER DEFAULT NULL,
    rating INTEGER,
    comment TEXT,
    date TEXT,
    deleted INTEGER DEFAULT 0,
    FOREIGN KEY(user_id) REFERENCES users(id),
    FOREIGN KEY(poem_id) REFERENCES poems(id),
    FOREIGN KEY(parent_id) REFERENCES comments(id)
)
""")

# ---------- POEM APPROVALS ----------
db.execute("""
CREATE TABLE IF NOT EXISTS poem_approvals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    poem_id INTEGER,
    approver_id INTEGER,
    date TEXT
)
""")

# ---------- FOLLOWERS ----------
db.execute("""
CREATE TABLE IF NOT EXISTS followers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    follower_id INTEGER,
    following_id INTEGER
)
""")

# ---------- NOTIFICATIONS ----------
db.execute("""
CREATE TABLE IF NOT EXISTS notifications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER,
    message TEXT,
    is_read INTEGER DEFAULT 0,
    date TEXT
)
""")

# ---------- USERNAME HISTORY ----------
db.execute("""
CREATE TABLE IF NOT EXISTS username_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER,
    old_username TEXT,
    change_date TEXT,
    FOREIGN KEY(user_id) REFERENCES users(id)
)
""")

# ---------- PROFILE PICTURE HISTORY ----------
db.execute("""
CREATE TABLE IF NOT EXISTS profile_picture_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER,
    old_picture TEXT,
    change_date TEXT,
    FOREIGN KEY(user_id) REFERENCES users(id)
)
""")

db.commit()
db.close()
print("Database initialized successfully with history tables!")
