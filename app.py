import os
import json
import secrets
import random
from collections import defaultdict
from datetime import datetime, timedelta
from functools import wraps

from flask import (
    Flask, abort, flash, jsonify, redirect,
    render_template, request, session, url_for
)
from werkzeug.security import check_password_hash, generate_password_hash
from werkzeug.utils import secure_filename
import psycopg2
import psycopg2.extras

from dotenv import load_dotenv
load_dotenv()

# ============================================================
# APP SETUP
# ============================================================
app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY") or secrets.token_hex(32)

UPLOAD_FOLDER = os.path.join("static", "uploads")
ALLOWED_EXTENSIONS = {"png", "jpg", "jpeg", "gif"}
app.config["UPLOAD_FOLDER"] = UPLOAD_FOLDER

# ============================================================
# HELPERS
# ============================================================
def allowed_file(filename):
    return (
        "." in filename and
        filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS
    )


def login_required(f):
    @wraps(f)
    def wrapped(*args, **kwargs):
        if "user_id" not in session:
            # Return JSON error for API/fetch requests, redirect for page requests
            if request.is_json or request.path.startswith("/messages"):
                return jsonify({"success": False, "error": "Not logged in"}), 401
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return wrapped


def is_strong_password(password):
    return (
        len(password) >= 8 and
        any(c.isupper() for c in password) and
        any(c.islower() for c in password) and
        any(c.isdigit() for c in password)
    )


# ============================================================
# DATABASE
# ============================================================
class _ScalarRow:
    """Wraps a RealDictRow so both [0] and ['col'] access work.
    [0] returns the first value (for COUNT(*) etc.)."""
    def __init__(self, row):
        self._row = row

    def __getitem__(self, key):
        if isinstance(key, int):
            return list(self._row.values())[key]
        return self._row[key]

    def __bool__(self):
        return bool(self._row)

    def __iter__(self):
        return iter(self._row)

    def keys(self):
        return self._row.keys()

    def get(self, key, default=None):
        return self._row.get(key, default)


class _CursorWrapper:
    """Wraps a psycopg2 cursor so fetchone() returns a _ScalarRow
    (supporting both index-0 and key access) and fetchall() returns
    a list of _ScalarRow objects."""
    def __init__(self, cur):
        self._cur = cur

    def fetchone(self):
        row = self._cur.fetchone()
        return _ScalarRow(row) if row is not None else None

    def fetchall(self):
        return [_ScalarRow(r) for r in self._cur.fetchall()]

    def __iter__(self):
        for row in self._cur:
            yield _ScalarRow(row)


# ============================================================
# DATABASE WRAPPER
# ============================================================
class DBWrapper:
    """Thin wrapper around a psycopg2 connection that mimics the
    sqlite3 connection API used throughout this app (.execute,
    .executescript, .commit, .close, row dict access)."""

    def __init__(self, conn):
        self._conn = conn
        self._cur  = conn.cursor()

    # ---- query helpers ----
    def execute(self, sql, params=()):
        self._cur.execute(sql, params)
        return _CursorWrapper(self._cur)

    def executescript(self, sql):
        """Run a multi-statement SQL block (no params).
        PostgreSQL requires each statement to be executed and committed separately."""
        for stmt in sql.split(";"):
            stmt = stmt.strip()
            if stmt:
                self._cur.execute(stmt)
                self._conn.commit()
        return self._cur

    def commit(self):
        self._conn.commit()

    def close(self):
        try:
            self._cur.close()
        except Exception:
            pass
        try:
            self._conn.close()
        except Exception:
            pass

    # Allow dict-style row access (rows already are RealDictRow)
    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()


def get_db():
    conn = psycopg2.connect(
        host=os.environ.get("DB_HOST", "localhost"),
        port=os.environ.get("DB_PORT", 5432),
        dbname=os.environ.get("DB_NAME", "poetry_db"),
        user=os.environ.get("DB_USER", "postgres"),
        password=os.environ.get("DB_PASSWORD", "")
    )
    conn.cursor_factory = psycopg2.extras.RealDictCursor
    return DBWrapper(conn)


# ============================================================
# DATABASE INIT
# ============================================================
def init_db():
    db = get_db()
    try:
        db.executescript("""
            CREATE TABLE IF NOT EXISTS users (
                id              SERIAL PRIMARY KEY,
                email           TEXT UNIQUE,
                username        TEXT UNIQUE NOT NULL,
                password        TEXT,
                profile_picture TEXT,
                is_admin        INTEGER DEFAULT 0,
                is_system_author INTEGER DEFAULT 0,
                can_login       INTEGER DEFAULT 1,
                reset_token     TEXT,
                reset_expires   TEXT,
                public_key      TEXT,
                key_created_at  TEXT
            );

            CREATE TABLE IF NOT EXISTS authors (
                id          SERIAL PRIMARY KEY,
                full_name   TEXT UNIQUE NOT NULL,
                bio         TEXT,
                birth_year  INTEGER,
                death_year  INTEGER,
                country     TEXT,
                period      TEXT,
                photo       TEXT,
                poem_count  INTEGER DEFAULT 0,
                avg_rating  REAL DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS poems (
                id             SERIAL PRIMARY KEY,
                title          TEXT NOT NULL,
                text           TEXT NOT NULL,
                genre          TEXT,
                language       TEXT,
                author         TEXT,
                date           TEXT,
                user_id        INTEGER,
                status         TEXT DEFAULT 'waiting',
                published_date TEXT,
                view_count     INTEGER DEFAULT 0,
                total_time_spent INTEGER DEFAULT 0,
                FOREIGN KEY (user_id) REFERENCES users(id)
            );

            CREATE TABLE IF NOT EXISTS badges (
                id              SERIAL PRIMARY KEY,
                name            TEXT UNIQUE NOT NULL,
                description     TEXT,
                icon            TEXT,
                category        TEXT,
                condition_type  TEXT,
                condition_value INTEGER,
                is_special      INTEGER DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS user_badges (
                id           SERIAL PRIMARY KEY,
                user_id      INTEGER NOT NULL,
                badge_id     INTEGER NOT NULL,
                date_awarded TIMESTAMP,
                is_featured  INTEGER DEFAULT 0,
                UNIQUE(user_id, badge_id),
                FOREIGN KEY (user_id)  REFERENCES users(id),
                FOREIGN KEY (badge_id) REFERENCES badges(id)
            );

            CREATE TABLE IF NOT EXISTS followers (
                id           SERIAL PRIMARY KEY,
                follower_id  INTEGER NOT NULL,
                following_id INTEGER NOT NULL,
                UNIQUE(follower_id, following_id),
                FOREIGN KEY (follower_id)  REFERENCES users(id),
                FOREIGN KEY (following_id) REFERENCES users(id)
            );

            CREATE TABLE IF NOT EXISTS comments (
                id        SERIAL PRIMARY KEY,
                poem_id   INTEGER NOT NULL,
                user_id   INTEGER NOT NULL,
                parent_id INTEGER,
                rating    INTEGER,
                comment   TEXT,
                date      TEXT,
                deleted   INTEGER DEFAULT 0,
                FOREIGN KEY (poem_id) REFERENCES poems(id),
                FOREIGN KEY (user_id) REFERENCES users(id)
            );

            CREATE TABLE IF NOT EXISTS poem_approvals (
                id          SERIAL PRIMARY KEY,
                poem_id     INTEGER NOT NULL,
                approver_id INTEGER NOT NULL,
                date        TEXT,
                UNIQUE(poem_id, approver_id),
                FOREIGN KEY (poem_id)     REFERENCES poems(id),
                FOREIGN KEY (approver_id) REFERENCES users(id)
            );

            CREATE TABLE IF NOT EXISTS poem_likes (
                id      SERIAL PRIMARY KEY,
                poem_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                UNIQUE(poem_id, user_id),
                FOREIGN KEY (poem_id) REFERENCES poems(id),
                FOREIGN KEY (user_id) REFERENCES users(id)
            );

            CREATE TABLE IF NOT EXISTS saved_poems (
                id      SERIAL PRIMARY KEY,
                poem_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                date    TEXT,
                UNIQUE(poem_id, user_id),
                FOREIGN KEY (poem_id) REFERENCES poems(id),
                FOREIGN KEY (user_id) REFERENCES users(id)
            );

            CREATE TABLE IF NOT EXISTS quotes (
                id          SERIAL PRIMARY KEY,
                user_id     INTEGER NOT NULL,
                poem_id     INTEGER,
                author      TEXT,
                poem_title  TEXT,
                quote_text  TEXT,
                description TEXT,
                type        TEXT,
                date        TEXT,
                likes       INTEGER DEFAULT 0,
                FOREIGN KEY (poem_id) REFERENCES poems(id),
                FOREIGN KEY (user_id) REFERENCES users(id)
            );

            CREATE TABLE IF NOT EXISTS quote_likes (
                id       SERIAL PRIMARY KEY,
                quote_id INTEGER NOT NULL,
                user_id  INTEGER NOT NULL,
                date     TEXT,
                UNIQUE(quote_id, user_id),
                FOREIGN KEY (quote_id) REFERENCES quotes(id),
                FOREIGN KEY (user_id)  REFERENCES users(id)
            );

            CREATE TABLE IF NOT EXISTS saved_quotes (
                id         SERIAL PRIMARY KEY,
                user_id    INTEGER NOT NULL,
                poem_id    INTEGER,
                quote_text TEXT,
                is_public  INTEGER DEFAULT 0,
                date       TEXT,
                FOREIGN KEY (user_id) REFERENCES users(id)
            );

            CREATE TABLE IF NOT EXISTS profile_picture_history (
                id          SERIAL PRIMARY KEY,
                user_id     INTEGER NOT NULL,
                old_picture TEXT,
                change_date TEXT,
                FOREIGN KEY (user_id) REFERENCES users(id)
            );

            CREATE TABLE IF NOT EXISTS username_history (
                id         SERIAL PRIMARY KEY,
                user_id    INTEGER NOT NULL,
                old_name   TEXT NOT NULL,
                changed_at TEXT,
                FOREIGN KEY (user_id) REFERENCES users(id)
            );

            CREATE TABLE IF NOT EXISTS collaborative_poems (
                id         SERIAL PRIMARY KEY,
                title      TEXT NOT NULL,
                creator_id INTEGER NOT NULL,
                created_at TEXT,
                FOREIGN KEY (creator_id) REFERENCES users(id)
            );

            CREATE TABLE IF NOT EXISTS collaborative_stanzas (
                id          SERIAL PRIMARY KEY,
                poem_id     INTEGER NOT NULL,
                user_id     INTEGER NOT NULL,
                stanza_text TEXT NOT NULL,
                position    INTEGER,
                created_at  TEXT,
                FOREIGN KEY (poem_id) REFERENCES collaborative_poems(id),
                FOREIGN KEY (user_id) REFERENCES users(id)
            );

            CREATE TABLE IF NOT EXISTS conversations (
                id         SERIAL PRIMARY KEY,
                user1_id   INTEGER NOT NULL,
                user2_id   INTEGER NOT NULL,
                created_at TEXT NOT NULL,
                UNIQUE(user1_id, user2_id)
            );
            CREATE TABLE IF NOT EXISTS messages (
                id                     SERIAL PRIMARY KEY,
                conversation_id        INTEGER NOT NULL,
                sender_id              INTEGER NOT NULL,
                receiver_id            INTEGER,
                message                TEXT DEFAULT '',
                is_read                INTEGER DEFAULT 0,
                date                   TEXT NOT NULL,
                encrypted_message      TEXT DEFAULT '',
                encrypted_key          TEXT DEFAULT '',
                iv                     TEXT DEFAULT '',
                encrypted_key_sender   TEXT DEFAULT '',
                encrypted_key_receiver TEXT DEFAULT '',
                FOREIGN KEY (conversation_id) REFERENCES conversations(id),
                FOREIGN KEY (sender_id) REFERENCES users(id)
            );
            CREATE TABLE IF NOT EXISTS notifications (
                id      SERIAL PRIMARY KEY,
                user_id INTEGER,
                message TEXT,
                is_read INTEGER DEFAULT 0,
                date    TEXT
            );

            -- Chain Poetry Salon
            CREATE TABLE IF NOT EXISTS chain_poems (
                id         SERIAL PRIMARY KEY,
                title      TEXT NOT NULL,
                creator_id INTEGER NOT NULL,
                status     TEXT NOT NULL DEFAULT 'active',
                created_at TEXT NOT NULL,
                ended_at   TEXT,
                FOREIGN KEY (creator_id) REFERENCES users(id)
            );
            CREATE TABLE IF NOT EXISTS chain_stanzas (
                id            SERIAL PRIMARY KEY,
                chain_poem_id INTEGER NOT NULL,
                user_id       INTEGER NOT NULL,
                stanza_text   TEXT NOT NULL,
                position      INTEGER NOT NULL,
                created_at    TEXT NOT NULL,
                FOREIGN KEY (chain_poem_id) REFERENCES chain_poems(id),
                FOREIGN KEY (user_id)       REFERENCES users(id)
            );

            -- Weekly Theme Salon
            CREATE TABLE IF NOT EXISTS weekly_themes (
                id         SERIAL PRIMARY KEY,
                title      TEXT NOT NULL,
                start_date TEXT NOT NULL,
                end_date   TEXT NOT NULL,
                created_by INTEGER NOT NULL,
                FOREIGN KEY (created_by) REFERENCES users(id)
            );
            CREATE TABLE IF NOT EXISTS weekly_theme_poems (
                id           SERIAL PRIMARY KEY,
                theme_id     INTEGER NOT NULL,
                user_id      INTEGER NOT NULL,
                title        TEXT NOT NULL,
                text         TEXT NOT NULL,
                submitted_at TEXT NOT NULL,
                FOREIGN KEY (theme_id) REFERENCES weekly_themes(id),
                FOREIGN KEY (user_id)  REFERENCES users(id)
            );

            -- Poet Duels
            CREATE TABLE IF NOT EXISTS duels (
                id            SERIAL PRIMARY KEY,
                theme_id      INTEGER NOT NULL,
                challenger_id INTEGER NOT NULL,
                challenged_id INTEGER NOT NULL,
                status        TEXT NOT NULL DEFAULT 'pending',
                winner_id     INTEGER,
                created_at    TEXT NOT NULL,
                FOREIGN KEY (theme_id)      REFERENCES weekly_themes(id),
                FOREIGN KEY (challenger_id) REFERENCES users(id),
                FOREIGN KEY (challenged_id) REFERENCES users(id)
            );
            CREATE TABLE IF NOT EXISTS duel_votes (
                id        SERIAL PRIMARY KEY,
                duel_id   INTEGER NOT NULL,
                voter_id  INTEGER NOT NULL,
                voted_for INTEGER NOT NULL,
                voted_at  TEXT NOT NULL,
                UNIQUE(duel_id, voter_id),
                FOREIGN KEY (duel_id)   REFERENCES duels(id),
                FOREIGN KEY (voter_id)  REFERENCES users(id),
                FOREIGN KEY (voted_for) REFERENCES users(id)
            );

            -- Hidden Poet Game
            CREATE TABLE IF NOT EXISTS daily_game_questions (
                id          SERIAL PRIMARY KEY,
                game_date   TEXT NOT NULL,
                poem_id     INTEGER NOT NULL,
                stanza_text TEXT NOT NULL,
                position    INTEGER NOT NULL,
                UNIQUE (game_date, position),
                FOREIGN KEY (poem_id) REFERENCES poems(id)
            );
            CREATE TABLE IF NOT EXISTS daily_game_attempts (
                id             SERIAL PRIMARY KEY,
                user_id        INTEGER NOT NULL,
                game_date      TEXT NOT NULL,
                question_pos   INTEGER NOT NULL,
                author_guess   TEXT,
                title_guess    TEXT,
                author_correct INTEGER DEFAULT 0,
                title_correct  INTEGER DEFAULT 0,
                attempted_at   TEXT NOT NULL,
                UNIQUE(user_id, game_date, question_pos),
                FOREIGN KEY (user_id) REFERENCES users(id)
            );
            CREATE TABLE IF NOT EXISTS daily_game_streaks (
                user_id        INTEGER PRIMARY KEY,
                current_streak INTEGER DEFAULT 0,
                best_streak    INTEGER DEFAULT 0,
                last_played    TEXT,
                total_correct  INTEGER DEFAULT 0,
                total_guesses  INTEGER DEFAULT 0,
                FOREIGN KEY (user_id) REFERENCES users(id)
            );

            -- Salon Circles
            CREATE TABLE IF NOT EXISTS salon_circles (
                id          SERIAL PRIMARY KEY,
                name        TEXT NOT NULL,
                description TEXT,
                owner_id    INTEGER NOT NULL,
                created_at  TEXT NOT NULL,
                FOREIGN KEY (owner_id) REFERENCES users(id)
            );
            CREATE TABLE IF NOT EXISTS circle_members (
                id        SERIAL PRIMARY KEY,
                circle_id INTEGER NOT NULL,
                user_id   INTEGER NOT NULL,
                role      TEXT NOT NULL DEFAULT 'member',
                joined_at TEXT NOT NULL,
                UNIQUE(circle_id, user_id),
                FOREIGN KEY (circle_id) REFERENCES salon_circles(id),
                FOREIGN KEY (user_id)   REFERENCES users(id)
            );
            CREATE TABLE IF NOT EXISTS circle_messages (
                id        SERIAL PRIMARY KEY,
                circle_id INTEGER NOT NULL,
                user_id   INTEGER NOT NULL,
                message   TEXT NOT NULL,
                sent_at   TEXT NOT NULL,
                FOREIGN KEY (circle_id) REFERENCES salon_circles(id),
                FOREIGN KEY (user_id)   REFERENCES users(id)
            );
            CREATE TABLE IF NOT EXISTS circle_posts (
                id        SERIAL PRIMARY KEY,
                circle_id INTEGER NOT NULL,
                user_id   INTEGER NOT NULL,
                poem_id   INTEGER,
                quote_id  INTEGER,
                body      TEXT,
                is_pinned INTEGER DEFAULT 0,
                posted_at TEXT NOT NULL,
                FOREIGN KEY (circle_id) REFERENCES salon_circles(id),
                FOREIGN KEY (user_id)   REFERENCES users(id)
            );

            -- Living Poem
            CREATE TABLE IF NOT EXISTS living_poem_lines (
                id          SERIAL PRIMARY KEY,
                line_number INTEGER NOT NULL,
                user_id     INTEGER NOT NULL,
                line_text   TEXT NOT NULL,
                added_at    TEXT NOT NULL,
                FOREIGN KEY (user_id) REFERENCES users(id)
            );
            CREATE TABLE IF NOT EXISTS living_poem_queue (
                id        SERIAL PRIMARY KEY,
                user_id   INTEGER NOT NULL UNIQUE,
                position  INTEGER NOT NULL,
                joined_at TEXT NOT NULL,
                deadline  TEXT,
                FOREIGN KEY (user_id) REFERENCES users(id)
            );
        """)
        # Safe migration: add public_key column if missing
        try:
            db.execute("ALTER TABLE users ADD COLUMN public_key TEXT")
            db.commit()
        except Exception:
            db._conn.rollback()

        # Safe migration: add encryption columns to messages table if missing
        messages_migrations = [
            "ALTER TABLE messages ADD COLUMN encrypted_key_sender TEXT NOT NULL DEFAULT ''",
            "ALTER TABLE messages ADD COLUMN encrypted_key_receiver TEXT NOT NULL DEFAULT ''",
            "ALTER TABLE messages ADD COLUMN iv TEXT NOT NULL DEFAULT ''",
            "ALTER TABLE messages ADD COLUMN encrypted_message TEXT NOT NULL DEFAULT ''",
        ]
        for sql in messages_migrations:
            try:
                db.execute(sql)
                db.commit()
            except Exception:
                db._conn.rollback()  # Column already exists

        db.commit()
    finally:
        db.close()


# ============================================================
# I18N — TRANSLATION SYSTEM
# ============================================================
SUPPORTED_LANGUAGES = ["en", "az", "tr", "ru"]
DEFAULT_LANGUAGE    = "en"
_translations: dict = {}

def _load_translations():
    """Load all JSON translation files into memory on startup."""
    base = os.path.join(os.path.dirname(__file__), "translations")
    for lang in SUPPORTED_LANGUAGES:
        path = os.path.join(base, f"{lang}.json")
        if os.path.exists(path):
            with open(path, encoding="utf-8") as f:
                _translations[lang] = json.load(f)

_load_translations()


def get_locale() -> str:
    """Return the active language code from session, defaulting to EN."""
    lang = session.get("lang", DEFAULT_LANGUAGE)
    return lang if lang in SUPPORTED_LANGUAGES else DEFAULT_LANGUAGE


def t(section: str, key: str, **kwargs) -> str:
    """Look up a translation string.
    Usage in Python:  t("nav", "home")
    Usage in Jinja:   {{ t("nav", "home") }}
    Supports .format() placeholders via kwargs.
    Falls back to EN, then to 'section.key'.
    """
    lang = get_locale()
    for L in (lang, DEFAULT_LANGUAGE):
        val = _translations.get(L, {}).get(section, {}).get(key)
        if val is not None:
            return val.format(**kwargs) if kwargs else val
    return f"{section}.{key}"


@app.route("/set-language/<lang>")
def set_language(lang: str):
    """Switch UI language and redirect back to the referring page."""
    if lang in SUPPORTED_LANGUAGES:
        session["lang"] = lang
    return redirect(request.args.get("next") or url_for("home"))


@app.context_processor
def inject_globals():
    return {
        "current_lang": get_locale(),
        "supported_languages": SUPPORTED_LANGUAGES
    }

# ============================================================
# CONTEXT PROCESSORS
# ============================================================
@app.context_processor
def inject_notification_count():
    count = 0
    if "user_id" in session:
        db = get_db()
        try:
            count = db.execute(
                "SELECT COUNT(*) FROM notifications WHERE user_id=%s AND is_read=0",
                (session["user_id"],)
            ).fetchone()[0]
        finally:
            db.close()
    return {
        "unread_notifications_count": count,
        "t": t,
        "current_lang": get_locale(),
        "supported_languages": SUPPORTED_LANGUAGES,
    }


# ============================================================
# SEED BADGES
# ============================================================
def seed_badges():
    db = get_db()
    try:
        badges = [
            ("Welcome",          "Publish your first poem",                   "🌱", "Author Badges",           "poems_published",       1,   0),
            ("Prolific Poet",    "Publish 10 poems",                          "✍",  "Author Badges",           "poems_published",       10,  0),
            ("Archivist Poet",   "Publish 40 poems",                          "📜", "Author Badges",           "poems_published",       40,  0),
            ("Popular Poet",     "One poem reaches 50 likes",                 "🔥", "Author Badges",           "single_poem_likes",     50,  0),
            ("Salon Favourite",  "Receive 300 total likes across your poems", "🏆", "Author Badges",           "total_poem_likes",      300, 0),
            ("Quote Master",     "Share 10 quotes",                           "✨", "Quote Badges",            "quotes_shared",         10,  0),
            ("Golden Line",      "One quote receives 30 likes",               "💎", "Quote Badges",            "single_quote_likes",    30,  0),
            ("Curator",          "Share quotes from 20 different poets",      "📖", "Quote Badges",            "different_poets_quoted",20,  0),
            ("Critic",           "Write 25 comments",                         "💬", "Community Badges",        "comments_written",      25,  0),
            ("Conversationalist","Write 10 comment replies",                  "🗣", "Community Badges",        "comment_replies",       10,  0),
            ("Thoughtful Reader","Write 10 long comments",                    "🧠", "Community Badges",        "long_comments",         10,  0),
            ("Quiet Reader",     "Save 25 poems or quotes",                   "🌙", "Reader Badges",           "total_saves",           25,  0),
            ("Archivist",        "Save 100 poems or quotes",                  "📚", "Reader Badges",           "total_saves",           100, 0),
            ("Connector",        "Follow 20 users",                           "🤝", "Social Badges",           "following_count",       20,  0),
            ("Admired Poet",     "Reach 50 followers",                        "⭐", "Social Badges",           "followers_count",       50,  0),
            ("Salon Star",       "Reach 100 followers",                       "🌟", "Social Badges",           "followers_count",       100, 0),
            ("Featured Poet",    "Given by the admin",                        "🏅", "Special Platform Badges", "manual",                None,1),
            ("Classical Voice",  "Awarded for contributing classical poetry", "🎭", "Special Platform Badges", "manual",                None,1),
            ("Early Member",     "One of the first 100 members of the salon","⏳", "Special Platform Badges", "manual",                None,1),
            # Duel badges
            ("Duelist",          "Win your first duel",                       "⚔",  "Duel Badges",             "duel_wins",             1,   0),
            ("Seasoned Duelist", "Win 10 duels",                              "🏆", "Duel Badges",             "duel_wins",             10,  0),
            ("Duel Legend",      "Win 50 duels",                              "👑", "Duel Badges",             "duel_wins",             50,  0),
            # Game badges
            ("Curious Reader",   "Get 50 correct guesses in Hidden Poet",     "🔍", "Game Badges",             "game_correct_guesses",  50,  0),
            ("Sharp Eye",        "Get 100 correct guesses in Hidden Poet",    "👁", "Game Badges",             "game_correct_guesses",  100, 0),
            ("Poetry Oracle",    "Get 500 correct guesses in Hidden Poet",    "🌟", "Game Badges",             "game_correct_guesses",  500, 0),
        ]
        for badge in badges:
            db.execute("""
                INSERT INTO badges
                (name, description, icon, category, condition_type, condition_value, is_special)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (name) DO NOTHING
            """, badge)
        db.commit()
    finally:
        db.close()


# ============================================================
# NOTIFICATIONS
# ============================================================
def create_notification(user_id, message):
    db = get_db()
    try:
        db.execute(
            "INSERT INTO notifications (user_id, message, is_read, date) VALUES (%s, %s, 0, %s)",
            (user_id, message, datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
        )
        db.commit()
    finally:
        db.close()


# ============================================================
# SYSTEM AUTHORS
# ============================================================
def get_or_create_system_author(username):
    db = get_db()
    try:
        user = db.execute("SELECT * FROM users WHERE username=%s", (username,)).fetchone()
        if user:
            user_id = user["id"]
        else:
            random_password = secrets.token_urlsafe(32)
            cursor = db.execute("""
                INSERT INTO users (username, password, is_admin, is_system_author, can_login)
                VALUES (%s, %s, 0, 1, 0)
                RETURNING id
            """, (username, generate_password_hash(random_password)))
            db.commit()
            user_id = cursor.fetchone()["id"]

        exists = db.execute("SELECT 1 FROM authors WHERE id=%s", (user_id,)).fetchone()
        if not exists:
            db.execute("INSERT INTO authors (id, full_name) VALUES (%s, %s)", (user_id, username))
            db.commit()

        return user_id
    finally:
        db.close()


# ============================================================
# STATS HELPERS
# ============================================================
def get_author_stats(author_id):
    db = get_db()
    try:
        stats = db.execute("""
            SELECT COUNT(p.id) AS poem_count, AVG(c.rating) AS avg_rating
            FROM poems p
            LEFT JOIN comments c
                ON p.id = c.poem_id AND c.parent_id IS NULL AND c.deleted=0
            WHERE p.user_id=%s AND p.status='published'
        """, (author_id,)).fetchone()
        return {
            "poem_count": stats["poem_count"] or 0,
            "avg_rating": round(stats["avg_rating"], 1) if stats["avg_rating"] else 0
        }
    finally:
        db.close()


def update_author_stats(author_id):
    db = get_db()
    try:
        poem_count = db.execute(
            "SELECT COUNT(*) FROM poems WHERE user_id=%s AND status='published'",
            (author_id,)
        ).fetchone()[0]

        avg_rating = db.execute("""
            SELECT AVG(c.rating)
            FROM comments c
            JOIN poems p ON c.poem_id = p.id
            WHERE p.user_id=%s AND c.deleted=0 AND c.parent_id IS NULL AND p.status='published'
        """, (author_id,)).fetchone()[0]

        avg_rating = round(avg_rating, 2) if avg_rating else 0

        db.execute("""
            INSERT INTO authors (id, full_name, poem_count, avg_rating)
            VALUES (%s, (SELECT username FROM users WHERE id=%s), %s, %s)
            ON CONFLICT(id) DO UPDATE SET
                poem_count=excluded.poem_count,
                avg_rating=excluded.avg_rating
        """, (author_id, author_id, poem_count, avg_rating))
        db.commit()
    finally:
        db.close()


def get_poem_avg_rating(poem_id):
    db = get_db()
    try:
        avg = db.execute("""
            SELECT AVG(rating) as avg_rating
            FROM comments
            WHERE poem_id=%s AND parent_id IS NULL AND deleted=0
        """, (poem_id,)).fetchone()["avg_rating"]
        return round(avg, 1) if avg else 0
    finally:
        db.close()


# ============================================================
# TOP CONTENT
# ============================================================
def get_top_content(period):
    db = get_db()
    now = datetime.now()

    period_map = {
        "week":  now - timedelta(days=7),
        "month": now - timedelta(days=30),
        "year":  now - timedelta(days=365),
    }
    start_date = period_map.get(period, datetime.min)
    start_date_str = start_date.strftime("%Y-%m-%d")

    try:
        top_poem = db.execute("""
            SELECT p.id, p.title, p.text, p.author, AVG(c.rating) AS avg_rating
            FROM poems p
            LEFT JOIN comments c
                ON p.id = c.poem_id AND c.parent_id IS NULL AND c.deleted = 0
            WHERE p.status = 'published' AND p.date >= %s
            GROUP BY p.id
            ORDER BY avg_rating DESC, p.date DESC
            LIMIT 1
        """, (start_date_str,)).fetchone()

        top_author = db.execute("""
            SELECT u.id, u.username, u.profile_picture,
                   COUNT(p.id) AS total_poems, AVG(c.rating) AS avg_rating
            FROM users u
            JOIN poems p ON u.id = p.user_id AND p.status='published' AND p.date >= %s
            LEFT JOIN comments c
                ON p.id = c.poem_id AND c.parent_id IS NULL AND c.deleted = 0
            GROUP BY u.id
            ORDER BY avg_rating DESC, total_poems DESC
            LIMIT 1
        """, (start_date_str,)).fetchone()

        return {
            "top_poem":   dict(top_poem)   if top_poem   else None,
            "top_author": dict(top_author) if top_author else None,
        }
    finally:
        db.close()


# ============================================================
# HOME
# ============================================================
@app.route("/")
def home():
    filter_by = request.args.get("filter", "week")
    db = get_db()
    try:
        recent_poems = db.execute("""
            SELECT * FROM poems
            WHERE status='published'
            ORDER BY date DESC
            LIMIT 8
        """).fetchall()
    finally:
        db.close()

    top_content = get_top_content(filter_by)
    return render_template(
        "index.html",
        poems=recent_poems,
        top_poem=top_content["top_poem"],
        top_author=top_content["top_author"],
        current_filter=filter_by
    )


@app.route("/filter_top")
def filter_top():
    period = request.args.get("period", "week")
    return jsonify(get_top_content(period))


# ============================================================
# SEARCH
# ============================================================
@app.route("/search")
def search():
    query        = request.args.get("search", "").strip()
    search_type  = request.args.get("search_type", "poem").strip()
    country_filter  = request.args.get("country", "").strip()
    period_filter   = request.args.get("period", "").strip()
    language_filter = request.args.get("language", "").strip()
    genre_filter    = request.args.get("genre", "").strip()
    genre_other     = request.args.get("genre_other", "").strip()

    if genre_filter == "other":
        genre_filter = genre_other if genre_other else ""

    db = get_db()
    authors, users, poems = [], [], []

    try:
        if search_type == "poem":
            # Authors (system)
            author_sql    = "SELECT DISTINCT a.* FROM authors a WHERE 1=1"
            author_params = []

            if query:
                author_sql += " AND a.full_name LIKE %s"
                author_params.append(f"%{query}%")
            if country_filter:
                author_sql += " AND a.country = %s"
                author_params.append(country_filter)
            if period_filter:
                author_sql += " AND a.period = %s"
                author_params.append(period_filter)

            if genre_filter or language_filter or query:
                author_sql += " AND EXISTS (SELECT 1 FROM poems p WHERE p.status='published' AND p.author=a.full_name"
                if genre_filter:
                    author_sql += " AND p.genre=%s"
                    author_params.append(genre_filter)
                if language_filter:
                    author_sql += " AND p.language=%s"
                    author_params.append(language_filter)
                if query:
                    author_sql += " AND (p.title LIKE %s OR p.author LIKE %s)"
                    author_params.extend([f"%{query}%", f"%{query}%"])
                author_sql += ")"

            authors = db.execute(author_sql, author_params).fetchall()

            # Poems
            poem_sql    = "SELECT p.* FROM poems p JOIN authors a ON p.author=a.full_name WHERE p.status='published'"
            poem_params = []

            if query:
                poem_sql += " AND (p.title LIKE %s OR p.author LIKE %s)"
                poem_params.extend([f"%{query}%", f"%{query}%"])
            if genre_filter:
                poem_sql += " AND p.genre=%s"
                poem_params.append(genre_filter)
            if language_filter:
                poem_sql += " AND p.language=%s"
                poem_params.append(language_filter)
            if country_filter:
                poem_sql += " AND a.country=%s"
                poem_params.append(country_filter)
            if period_filter:
                poem_sql += " AND a.period=%s"
                poem_params.append(period_filter)

            poems = db.execute(poem_sql, poem_params).fetchall()

        elif search_type == "user":
            user_sql    = "SELECT * FROM users WHERE is_system_author=0"
            user_params = []
            if query:
                user_sql += " AND username LIKE %s"
                user_params.append(f"%{query}%")
            users = db.execute(user_sql, user_params).fetchall()

    finally:
        db.close()

    return render_template(
        "search_results.html",
        query=query,
        search_type=search_type,
        authors=authors,
        users=users,
        poems=poems,
        country_filter=country_filter,
        period_filter=period_filter,
        genre_filter=genre_filter,
        language_filter=language_filter,
        genre_filter_other=(genre_other if request.args.get("genre") == "other" else ""),
    )


# ============================================================
# AUTH — REGISTER
# ============================================================
@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        email    = request.form.get("email", "").strip()
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")

        if not email or not username or not password:
            return render_template("register.html", error="All fields are required.")

        if not is_strong_password(password):
            return render_template(
                "register.html",
                error="Password must be 8+ characters and include uppercase, lowercase, and a number."
            )

        db = get_db()
        try:
            exists = db.execute(
                "SELECT 1 FROM users WHERE username=%s OR email=%s",
                (username, email)
            ).fetchone()
            if exists:
                return render_template("register.html", error="Username or email already exists.")

            db.execute("""
                INSERT INTO users (email, username, password, is_admin, is_system_author, can_login)
                VALUES (%s, %s, %s, 0, 0, 1)
            """, (email, username, generate_password_hash(password)))
            db.commit()
        finally:
            db.close()

        return redirect(url_for("login"))

    return render_template("register.html")


# ============================================================
# AUTH — LOGIN
# ============================================================
@app.route("/login", methods=["GET", "POST"])
def login():
    # Preserve language preference across session clear
    _lang = session.get("lang")
    session.clear()
    if _lang:
        session["lang"] = _lang
    if request.method == "POST":
        identifier = request.form.get("username", "").strip()
        password   = request.form.get("password", "")

        if not identifier or not password:
            return render_template("login.html", error="Please provide username/email and password.")

        db = get_db()
        try:
            user = db.execute(
                "SELECT * FROM users WHERE username=%s OR email=%s",
                (identifier, identifier)
            ).fetchone()
        finally:
            db.close()

        if not user:
            return render_template("login.html", error="Invalid credentials.", show_forgot=True)
        if user["is_system_author"]:
            return render_template("login.html", error="This account cannot log in.")
        if not user["can_login"]:
            return render_template("login.html", error="This account is disabled.")
        if not check_password_hash(user["password"], password):
            return render_template("login.html", error="Invalid credentials.", show_forgot=True)

        session["user_id"]  = user["id"]
        session["username"] = user["username"]
        session["is_admin"] = bool(user["is_admin"])
        return redirect(url_for("home"))

    return render_template("login.html")


# ============================================================
# AUTH — FORGOT PASSWORD
# ============================================================
@app.route("/forgot-password", methods=["GET", "POST"])
def forgot_password():
    if request.method == "POST":
        email = request.form.get("email", "").strip()
        if not email:
            return render_template("forgot_password.html", error="Please enter your email.")

        db = get_db()
        try:
            user = db.execute("SELECT * FROM users WHERE email=%s", (email,)).fetchone()
            if user:
                token   = secrets.token_urlsafe(32)
                expires = (datetime.now() + timedelta(hours=1)).isoformat()
                db.execute(
                    "UPDATE users SET reset_token=%s, reset_expires=%s WHERE id=%s",
                    (token, expires, user["id"])
                )
                db.commit()
                # TODO: send real email
                print(f"[DEV] Password reset: http://localhost:5000/reset/{token}")
        finally:
            db.close()

        return render_template(
            "forgot_password.html",
            success="If that email exists, a reset link has been sent."
        )

    return render_template("forgot_password.html")


# ============================================================
# AUTH — RESET PASSWORD
# ============================================================
@app.route("/reset/<token>", methods=["GET", "POST"])
def reset_password(token):
    db = get_db()
    try:
        user = db.execute("SELECT * FROM users WHERE reset_token=%s", (token,)).fetchone()
        if not user:
            abort(404)
        if datetime.now() > datetime.fromisoformat(user["reset_expires"]):
            abort(403)

        if request.method == "POST":
            password = request.form.get("password", "")
            if not is_strong_password(password):
                return render_template(
                    "reset_password.html",
                    error="Password must be 8+ characters with uppercase, lowercase, and a number."
                )
            db.execute(
                "UPDATE users SET password=%s, reset_token=NULL, reset_expires=NULL WHERE id=%s",
                (generate_password_hash(password), user["id"])
            )
            db.commit()
            return redirect(url_for("login"))
    finally:
        db.close()

    return render_template("reset_password.html")




# ============================================================
# UPLOAD POEM
# ============================================================
@app.route("/upload", methods=["GET", "POST"])
@login_required
def upload():
    if request.method == "POST":
        title    = request.form.get("title", "").strip()
        text     = request.form.get("text", "").strip()
        genre    = request.form.get("genre", "").strip()
        language = request.form.get("language", "").strip()

        if not title or not text or not genre or not language:
            return render_template("upload.html", error="All fields are required.")

        db = get_db()
        try:
            db.execute("""
                INSERT INTO poems (title, text, genre, language, author, date, user_id, status)
                VALUES (%s, %s, %s, %s, %s, %s, %s, 'waiting')
            """, (
                title, text, genre, language,
                session["username"],
                datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                session["user_id"]
            ))
            db.commit()
        finally:
            db.close()
        return redirect(url_for("waiting_salon"))

    return render_template("upload.html")


# ============================================================
# EDIT POEM
# ============================================================
def can_edit_poem(poem, user_id, is_admin):
    return poem["user_id"] == user_id or is_admin


@app.route("/poem/<int:poem_id>/edit", methods=["GET", "POST"])
@login_required
def edit_poem(poem_id):
    db = get_db()
    try:
        poem = db.execute("SELECT * FROM poems WHERE id=%s", (poem_id,)).fetchone()
        if not poem:
            abort(404)

        is_admin = session.get("is_admin", False)
        if not can_edit_poem(poem, session["user_id"], is_admin):
            abort(403)

        owner_id = poem["user_id"]

        if request.method == "POST":
            title = request.form.get("title", "").strip()
            text  = request.form.get("text", "").rstrip()

            if not title or not text:
                return render_template("edit_poem.html", poem=poem, owner_id=owner_id,
                                       error="Title and text are required.")

            db.execute("UPDATE poems SET title=%s, text=%s WHERE id=%s", (title, text, poem_id))
            db.commit()
            return redirect(url_for("profile", user_id=owner_id))
    finally:
        db.close()

    return render_template("edit_poem.html", poem=poem, owner_id=owner_id)


# ============================================================
# WAITING SALON
# ============================================================
@app.route("/waiting-salon")
@login_required
def waiting_salon():
    db = get_db()
    try:
        poems      = db.execute("SELECT * FROM poems WHERE status='waiting'").fetchall()
        avg_rating = db.execute("""
            SELECT COALESCE(AVG(c.rating), 0) as avg_rating
            FROM comments c
            JOIN poems p ON c.poem_id = p.id
            WHERE p.user_id=%s
        """, (session["user_id"],)).fetchone()["avg_rating"]

        user_data = db.execute("SELECT is_admin FROM users WHERE id=%s", (session["user_id"],)).fetchone()
        is_admin  = bool(user_data and user_data["is_admin"])
    finally:
        db.close()

    return render_template(
        "waiting_salon.html",
        poems=poems,
        avg_rating=round(avg_rating, 2),
        is_admin=is_admin
    )


# ============================================================
# APPROVE / REJECT POEM
# ============================================================
def _get_user_avg_rating(db, user_id):
    return db.execute("""
        SELECT COALESCE(AVG(c.rating), 0) as avg_rating
        FROM comments c
        JOIN poems p ON c.poem_id = p.id
        WHERE p.user_id=%s
    """, (user_id,)).fetchone()["avg_rating"]


@app.route("/approve/<int:poem_id>", methods=["POST"])
@login_required
def approve_poem(poem_id):
    db = get_db()
    try:
        user_id  = session["user_id"]
        row      = db.execute("SELECT is_admin FROM users WHERE id=%s", (user_id,)).fetchone()
        is_admin = bool(row and row["is_admin"])
        avg_rating = _get_user_avg_rating(db, user_id)

        if not is_admin and avg_rating <= 4.5:
            return "You need an average rating above 4.5 to approve poems.", 403

        poem = db.execute("SELECT * FROM poems WHERE id=%s", (poem_id,)).fetchone()
        if not poem:
            abort(404)
        if poem["user_id"] == user_id:
            return "You cannot approve your own poem.", 403

        exists = db.execute(
            "SELECT 1 FROM poem_approvals WHERE poem_id=%s AND approver_id=%s",
            (poem_id, user_id)
        ).fetchone()
        if exists:
            return "You have already approved this poem.", 403

        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        db.execute(
            "INSERT INTO poem_approvals (poem_id, approver_id, date) VALUES (%s, %s, %s)",
            (poem_id, user_id, now_str)
        )
        db.execute(
            "UPDATE poems SET status='published', published_date=%s WHERE id=%s",
            (now_str[:10], poem_id)
        )
        db.execute(
            "INSERT INTO notifications (user_id, message, is_read, date) VALUES (%s, %s, 0, %s)",
            (poem["user_id"], f"Your poem '{poem['title']}' was approved 🎉", now_str)
        )
        db.commit()
    finally:
        db.close()

    db = get_db()
    try:
        check_and_award_badges(db, poem["user_id"])
        db.commit()
    finally:
        db.close()
    return redirect(url_for("waiting_salon"))


@app.route("/reject/<int:poem_id>", methods=["POST"])
@login_required
def reject_poem(poem_id):
    db = get_db()
    try:
        user_id  = session["user_id"]
        row      = db.execute("SELECT is_admin FROM users WHERE id=%s", (user_id,)).fetchone()
        is_admin = bool(row and row["is_admin"])
        avg_rating = _get_user_avg_rating(db, user_id)

        if not is_admin and avg_rating <= 4.5:
            return "You need an average rating above 4.5 to reject poems.", 403

        poem = db.execute("SELECT * FROM poems WHERE id=%s", (poem_id,)).fetchone()
        if not poem:
            abort(404)
        if poem["user_id"] == user_id:
            return "You cannot reject your own poem.", 403

        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        db.execute("UPDATE poems SET status='rejected' WHERE id=%s", (poem_id,))
        db.execute(
            "INSERT INTO notifications (user_id, message, is_read, date) VALUES (%s, %s, 0, %s)",
            (poem["user_id"], f"Your poem '{poem['title']}' was rejected ❌", now_str)
        )
        db.commit()
    finally:
        db.close()

    return redirect(url_for("waiting_salon"))


# ============================================================
# WAITING POEM DETAIL
# ============================================================
@app.route("/waiting-poem/<int:poem_id>")
@login_required
def waiting_poem_detail(poem_id):
    db = get_db()
    try:
        poem = db.execute("SELECT * FROM poems WHERE id=%s", (poem_id,)).fetchone()
        if not poem:
            abort(404)

        avg_rating = _get_user_avg_rating(db, session["user_id"])
        row        = db.execute("SELECT is_admin FROM users WHERE id=%s", (session["user_id"],)).fetchone()
        is_admin   = bool(row and row["is_admin"])
    finally:
        db.close()

    return render_template(
        "waiting_poem_detail.html",
        poem=poem,
        avg_rating=round(avg_rating, 2),
        is_admin=is_admin
    )


# ============================================================
# POEM DETAIL
# ============================================================
@app.route("/poem_time", methods=["POST"])
def poem_time():
    try:
        data = json.loads(request.data)
    except (json.JSONDecodeError, ValueError):
        data = {}

    poem_id    = data.get("poem_id")
    time_spent = data.get("time_spent", 0)

    if poem_id and isinstance(time_spent, (int, float)) and time_spent > 0:
        db = get_db()
        try:
            db.execute(
                "UPDATE poems SET total_time_spent = total_time_spent + %s WHERE id=%s",
                (time_spent, poem_id)
            )
            db.commit()
        finally:
            db.close()

    return jsonify({"status": "ok"})


@app.route("/poem/<int:poem_id>", methods=["GET", "POST"])
def poem_detail(poem_id):
    db = get_db()
    try:
        poem = db.execute(
            "SELECT * FROM poems WHERE id=%s AND status='published'",
            (poem_id,)
        ).fetchone()

        if not poem:
            abort(404)

        back_url = request.referrer

        db.execute("UPDATE poems SET view_count = view_count + 1 WHERE id=%s", (poem_id,))
        db.commit()

        if request.method == "POST" and "user_id" in session:
            rating       = int(request.form.get("rating") or 0)
            comment_text = request.form.get("comment", "").strip()
            parent_id    = request.form.get("parent_id") or None
            reply_tag    = request.form.get("reply_tag", "").strip()

            if comment_text or rating > 0:
                if reply_tag and comment_text:
                    comment_text = f"@{reply_tag} {comment_text}"

                db.execute("""
                    INSERT INTO comments (poem_id, user_id, parent_id, rating, comment, date, deleted)
                    VALUES (%s, %s, %s, %s, %s, %s, 0)
                """, (
                    poem_id,
                    session["user_id"],
                    parent_id,
                    rating if not parent_id else None,
                    comment_text,
                    datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                ))
                db.commit()

                commenter = db.execute(
                    "SELECT username FROM users WHERE id=%s", (session["user_id"],)
                ).fetchone()

                now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

                if not parent_id and poem["user_id"] != session["user_id"] and commenter:
                    db.execute("""
                        INSERT INTO notifications (user_id, message, is_read, date)
                        VALUES (%s, %s, 0, %s)
                    """, (
                        poem["user_id"],
                        f"{commenter['username']} commented on your poem '{poem['title']}'",
                        now_str
                    ))
                    db.commit()

                if parent_id:
                    parent = db.execute(
                        "SELECT user_id FROM comments WHERE id=%s", (parent_id,)
                    ).fetchone()
                    if parent and parent["user_id"] != session["user_id"] and commenter:
                        db.execute("""
                            INSERT INTO notifications (user_id, message, is_read, date)
                            VALUES (%s, %s, 0, %s)
                        """, (
                            parent["user_id"],
                            f"{commenter['username']} replied to your comment on '{poem['title']}'",
                            now_str
                        ))
                        db.commit()

                if not parent_id and rating > 0:
                    update_author_stats(poem["user_id"])

                check_and_award_badges(db, session["user_id"])
                db.commit()
                return redirect(url_for("poem_detail", poem_id=poem_id))

        # Saved%s
        is_saved = False
        if "user_id" in session:
            is_saved = bool(db.execute(
                "SELECT 1 FROM saved_poems WHERE user_id=%s AND poem_id=%s",
                (session["user_id"], poem_id)
            ).fetchone())

        # Comments
        raw_comments = db.execute("""
            SELECT c.id AS comment_id, c.comment, c.user_id, c.deleted, c.date, u.username
            FROM comments c
            JOIN users u ON c.user_id = u.id
            WHERE c.poem_id=%s AND c.parent_id IS NULL AND c.deleted=0
            ORDER BY c.date ASC
        """, (poem_id,)).fetchall()

        comments = []
        for c in raw_comments:
            comment = dict(c)
            replies = db.execute("""
                SELECT c.id AS comment_id, c.comment, c.user_id, c.deleted, c.date, u.username
                FROM comments c
                JOIN users u ON c.user_id = u.id
                WHERE c.parent_id=%s AND c.deleted=0
                ORDER BY c.date ASC
            """, (c["comment_id"],)).fetchall()
            comment["replies"] = [dict(r) for r in replies]
            comments.append(comment)

        avg = db.execute("""
            SELECT AVG(rating) FROM comments
            WHERE poem_id=%s AND parent_id IS NULL AND deleted=0
        """, (poem_id,)).fetchone()[0]
        avg_rating = round(avg, 1) if avg else None

    finally:
        db.close()

    return render_template(
        "poem_detail.html",
        poem=poem,
        comments=comments,
        avg_rating=avg_rating,
        current_user_id=session.get("user_id"),
        back_url=back_url,
        view_count=poem["view_count"] or 0,
        total_time_spent=poem["total_time_spent"] or 0,
        is_saved=is_saved
    )


@app.route("/delete_comment/<int:comment_id>", methods=["POST"])
@login_required
def delete_comment(comment_id):
    db = get_db()
    try:
        comment = db.execute("SELECT * FROM comments WHERE id=%s", (comment_id,)).fetchone()
        if not comment:
            abort(404)
        poem = db.execute("SELECT * FROM poems WHERE id=%s", (comment["poem_id"],)).fetchone()
        if session["user_id"] != comment["user_id"] and session["user_id"] != poem["user_id"]:
            abort(403)
        db.execute(
            "UPDATE comments SET deleted=1 WHERE id=%s OR parent_id=%s",
            (comment_id, comment_id)
        )
        db.commit()
        poem_id = comment["poem_id"]
    finally:
        db.close()

    return redirect(url_for("poem_detail", poem_id=poem_id))


# ============================================================
# SAVE POEM / QUOTE
# ============================================================
@app.route("/toggle_save_poem/<int:poem_id>", methods=["POST"])
@login_required
def toggle_save_poem(poem_id):
    db = get_db()
    try:
        poem = db.execute(
            "SELECT id FROM poems WHERE id=%s AND status='published'", (poem_id,)
        ).fetchone()
        if not poem:
            return jsonify({"success": False, "error": "Poem not found"}), 404

        existing = db.execute(
            "SELECT id FROM saved_poems WHERE user_id=%s AND poem_id=%s",
            (session["user_id"], poem_id)
        ).fetchone()

        if existing:
            db.execute("DELETE FROM saved_poems WHERE user_id=%s AND poem_id=%s",
                       (session["user_id"], poem_id))
            action = "unsaved"
        else:
            db.execute(
                "INSERT INTO saved_poems (user_id, poem_id, date) VALUES (%s, %s, %s)",
                (session["user_id"], poem_id, datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
            )
            action = "saved"

        check_and_award_badges(db, session["user_id"])
        db.commit()
    finally:
        db.close()

    return jsonify({"success": True, "action": action})


@app.route("/save_quote", methods=["POST"])
@login_required
def save_quote():
    quote_text = request.form.get("quote_text", "").strip()
    poem_id    = request.form.get("poem_id")

    if not quote_text or not poem_id:
        return jsonify({"success": False, "error": "Missing quote or poem"}), 400

    db = get_db()
    try:
        poem = db.execute(
            "SELECT id FROM poems WHERE id=%s AND status='published'", (poem_id,)
        ).fetchone()
        if not poem:
            return jsonify({"success": False, "error": "Poem not found"}), 404

        db.execute(
            "INSERT INTO saved_quotes (user_id, poem_id, quote_text, date) VALUES (%s, %s, %s, %s)",
            (session["user_id"], poem_id, quote_text, datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
        )
        db.commit()
    finally:
        db.close()

    return jsonify({"success": True})


# ============================================================
# PROFILE & FOLLOW
# ============================================================
@app.route("/profile/<int:user_id>")
def profile(user_id):
    db = get_db()
    try:
        user = db.execute("SELECT * FROM users WHERE id=%s", (user_id,)).fetchone()
        if not user:
            abort(404)

        author_bio = None
        if user["is_system_author"]:
            row = db.execute("SELECT bio FROM authors WHERE id=%s", (user_id,)).fetchone()
            if row and row["bio"]:
                author_bio = row["bio"]

        rows = db.execute("""
            SELECT * FROM poems WHERE user_id=%s AND status='published' ORDER BY id DESC
        """, (user_id,)).fetchall()

        poems_by_genre = {}
        for poem in rows:
            poems_by_genre.setdefault(poem["genre"], []).append(poem)

        saved_poems, saved_quotes = [], []
        if "user_id" in session and session["user_id"] == user_id:
            saved_poems = db.execute("""
                SELECT p.*, sp.date AS saved_date
                FROM saved_poems sp
                JOIN poems p ON sp.poem_id = p.id
                WHERE sp.user_id=%s AND p.status='published'
                ORDER BY sp.date DESC
            """, (user_id,)).fetchall()

            saved_quotes = db.execute("""
                SELECT sq.*, p.title, p.author
                FROM saved_quotes sq
                JOIN poems p ON sq.poem_id = p.id
                WHERE sq.user_id=%s AND p.status='published'
                ORDER BY sq.date DESC
            """, (user_id,)).fetchall()

        featured_badges = db.execute("""
            SELECT b.id, b.name, b.icon
            FROM user_badges ub
            JOIN badges b ON ub.badge_id = b.id
            WHERE ub.user_id=%s AND ub.is_featured=1
            ORDER BY ub.date_awarded DESC
            LIMIT 3
        """, (user_id,)).fetchall()

        waiting   = db.execute("SELECT COUNT(*) FROM poems WHERE user_id=%s AND status='waiting'", (user_id,)).fetchone()[0]
        followers = db.execute("SELECT COUNT(*) FROM followers WHERE following_id=%s", (user_id,)).fetchone()[0]
        following = db.execute("SELECT COUNT(*) FROM followers WHERE follower_id=%s", (user_id,)).fetchone()[0]

        rating_row = db.execute("""
            SELECT AVG(c.rating) FROM comments c
            JOIN poems p ON c.poem_id = p.id
            WHERE p.user_id=%s AND c.parent_id IS NULL AND c.deleted=0
        """, (user_id,)).fetchone()[0]
        rating = round(rating_row, 1) if rating_row else 0

        already_following = False
        if "user_id" in session:
            already_following = bool(db.execute(
                "SELECT 1 FROM followers WHERE follower_id=%s AND following_id=%s",
                (session["user_id"], user_id)
            ).fetchone())

    finally:
        db.close()

    return render_template(
        "profile.html",
        user=user,
        poems_by_genre=poems_by_genre,
        saved_poems=saved_poems,
        saved_quotes=saved_quotes,
        waiting=waiting,
        followers=followers,
        following=following,
        rating=rating,
        already_following=already_following,
        author_bio=author_bio,
        featured_badges=featured_badges
    )


@app.route("/profile/<int:user_id>/followers")
def get_followers(user_id):
    db = get_db()
    try:
        rows = db.execute("""
            SELECT u.id, u.username
            FROM followers f
            JOIN users u ON f.follower_id = u.id
            WHERE f.following_id=%s
            ORDER BY LOWER(u.username)
        """, (user_id,)).fetchall()
        return jsonify({"users": [{"id": r["id"], "username": r["username"]} for r in rows]})
    except Exception as e:
        return jsonify({"error": str(e), "users": []}), 500
    finally:
        db.close()


@app.route("/profile/<int:user_id>/following")
def get_following(user_id):
    db = get_db()
    try:
        rows = db.execute("""
            SELECT u.id, u.username
            FROM followers f
            JOIN users u ON f.following_id = u.id
            WHERE f.follower_id=%s
            ORDER BY LOWER(u.username)
        """, (user_id,)).fetchall()
        return jsonify({"users": [{"id": r["id"], "username": r["username"]} for r in rows]})
    except Exception as e:
        return jsonify({"error": str(e), "users": []}), 500
    finally:
        db.close()


@app.route("/follow/<int:user_id>", methods=["POST"])
@login_required
def toggle_follow(user_id):
    current_user = session["user_id"]
    if current_user == user_id:
        abort(403)

    db = get_db()
    try:
        exists = db.execute(
            "SELECT 1 FROM followers WHERE follower_id=%s AND following_id=%s",
            (current_user, user_id)
        ).fetchone()

        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        if exists:
            db.execute("DELETE FROM followers WHERE follower_id=%s AND following_id=%s",
                       (current_user, user_id))
            action = "unfollowed"
        else:
            db.execute("INSERT INTO followers (follower_id, following_id) VALUES (%s, %s)",
                       (current_user, user_id))
            action = "followed"
            follower = db.execute("SELECT username FROM users WHERE id=%s", (current_user,)).fetchone()
            if follower:
                db.execute("""
                    INSERT INTO notifications (user_id, message, is_read, date)
                    VALUES (%s, %s, 0, %s)
                """, (user_id, f"{follower['username']} started following you", now_str))
        
        check_and_award_badges(db, current_user)
        check_and_award_badges(db, user_id)
        db.commit()

        profile_followers = db.execute(
            "SELECT COUNT(*) FROM followers WHERE following_id=%s", (user_id,)
        ).fetchone()[0]
        profile_following = db.execute(
            "SELECT COUNT(*) FROM followers WHERE follower_id=%s", (user_id,)
        ).fetchone()[0]

    finally:
        db.close()

    return jsonify({
        "action": action,
        "followers": profile_followers,
        "following": profile_following
    })


# ============================================================
# EDIT PROFILE
# ============================================================
@app.route("/edit_profile", methods=["POST"])
@login_required
def edit_profile():
    user_id = session["user_id"]
    db = get_db()
    try:
        user = db.execute("SELECT * FROM users WHERE id=%s", (user_id,)).fetchone()
        if not user:
            return jsonify({"success": False, "error": "User not found"})

        username = request.form.get("username", "").strip()
        if username and username != user["username"]:
            conflict = db.execute(
                "SELECT 1 FROM users WHERE username=%s AND id!=%s", (username, user_id)
            ).fetchone()
            if conflict:
                return jsonify({"success": False, "error": "Username already taken"})
            db.execute("UPDATE users SET username=%s WHERE id=%s", (username, user_id))
            session["username"] = username

        new_filename = user["profile_picture"]
        file = request.files.get("profile_picture")
        if file and file.filename and allowed_file(file.filename):
            filename = f"user_{user_id}_{secure_filename(file.filename)}"
            os.makedirs(app.config["UPLOAD_FOLDER"], exist_ok=True)
            file_path = os.path.join(app.config["UPLOAD_FOLDER"], filename)

            # Remove old file if it exists and is not the default
            if user["profile_picture"] and user["profile_picture"] not in ("default_user.jpg",):
                old_path = os.path.join(app.config["UPLOAD_FOLDER"], user["profile_picture"])
                if os.path.exists(old_path):
                    os.remove(old_path)

            file.save(file_path)
            new_filename = filename
            db.execute("UPDATE users SET profile_picture=%s WHERE id=%s", (new_filename, user_id))

        db.commit()
    finally:
        db.close()

    return jsonify({
        "success": True,
        "username": username or user["username"],
        "profile_picture": new_filename
    })


@app.route("/remove_profile_photo", methods=["POST"])
@login_required
def remove_profile_photo():
    user_id = session["user_id"]
    db = get_db()
    try:
        user = db.execute("SELECT * FROM users WHERE id=%s", (user_id,)).fetchone()
        if not user:
            return jsonify({"success": False, "error": "User not found"})

        if user["profile_picture"] and user["profile_picture"] not in ("default_user.jpg",):
            path = os.path.join(app.config["UPLOAD_FOLDER"], user["profile_picture"])
            if os.path.exists(path):
                os.remove(path)

        db.execute("UPDATE users SET profile_picture='default_user.jpg' WHERE id=%s", (user_id,))
        db.commit()
    finally:
        db.close()

    return jsonify({"success": True})


# ============================================================
# SAVINGS
# ============================================================
@app.route("/savings")
@login_required
def savings():
    db = get_db()
    try:
        saved_poems = db.execute("""
            SELECT p.*, sp.date AS saved_date
            FROM saved_poems sp
            JOIN poems p ON sp.poem_id = p.id
            WHERE sp.user_id=%s
            ORDER BY sp.date DESC
        """, (session["user_id"],)).fetchall()

        saved_quotes = db.execute("""
            SELECT sq.*, p.title, p.author
            FROM saved_quotes sq
            JOIN poems p ON sq.poem_id = p.id
            WHERE sq.user_id=%s
            ORDER BY sq.date DESC
        """, (session["user_id"],)).fetchall()

        # Feature 3: user's own collections (public + private)
        user_collections = []
        if _table_exists(db, "collections"):
            user_collections = db.execute("""
                SELECT c.*,
                       (SELECT COUNT(*) FROM collection_poems WHERE collection_id=c.id) AS poem_count
                FROM collections c
                WHERE c.user_id=%s
                ORDER BY c.created_at DESC
            """, (session["user_id"],)).fetchall()
    finally:
        db.close()

    return render_template("savings.html",
                           saved_poems=saved_poems,
                           saved_quotes=saved_quotes,
                           user_collections=user_collections)


# ============================================================
# NOTIFICATIONS
# ============================================================
@app.route("/notifications")
@login_required
def notifications_page():
    db = get_db()
    try:
        notifications = db.execute("""
            SELECT * FROM notifications WHERE user_id=%s
            ORDER BY date DESC, id DESC
        """, (session["user_id"],)).fetchall()
    finally:
        db.close()
    return render_template("notifications.html", notifications=notifications)


@app.route("/notifications/read/<int:notification_id>", methods=["POST"])
@login_required
def mark_notification_read(notification_id):
    db = get_db()
    try:
        db.execute(
            "UPDATE notifications SET is_read=1 WHERE id=%s AND user_id=%s",
            (notification_id, session["user_id"])
        )
        db.commit()
    finally:
        db.close()
    return jsonify({"success": True, "notification_id": notification_id})


@app.route("/notifications/read_all", methods=["POST"])
@login_required
def mark_all_notifications_read():
    db = get_db()
    try:
        db.execute("UPDATE notifications SET is_read=1 WHERE user_id=%s", (session["user_id"],))
        db.commit()
    finally:
        db.close()
    return jsonify({"success": True})


@app.route("/notifications/unread_count")
def unread_notifications_count():
    if "user_id" not in session:
        return jsonify({"count": 0})
    db = get_db()
    try:
        count = db.execute(
            "SELECT COUNT(*) FROM notifications WHERE user_id=%s AND is_read=0",
            (session["user_id"],)
        ).fetchone()[0]
    finally:
        db.close()
    return jsonify({"count": count})


# ============================================================
# QUOTES
# ============================================================
@app.route("/quotes")
def quotes():
    db = get_db()
    try:
        rows = db.execute("""
            SELECT q.*, u.username, COUNT(ql.id) AS like_count
            FROM quotes q
            JOIN users u ON q.user_id = u.id
            LEFT JOIN quote_likes ql ON q.id = ql.quote_id
            GROUP BY q.id
            ORDER BY q.date DESC, q.id DESC
        """).fetchall()

        liked_quote_ids  = set()
        saved_quote_keys = set()

        if "user_id" in session:
            liked_rows = db.execute(
                "SELECT quote_id FROM quote_likes WHERE user_id=%s", (session["user_id"],)
            ).fetchall()
            liked_quote_ids = {r["quote_id"] for r in liked_rows}

            saved_rows = db.execute(
                "SELECT poem_id, quote_text FROM saved_quotes WHERE user_id=%s",
                (session["user_id"],)
            ).fetchall()
            saved_quote_keys = {f"{r['poem_id']}|||{r['quote_text']}" for r in saved_rows}

        quotes_list = []
        for row in rows:
            q = dict(row)
            q["is_liked"] = q["id"] in liked_quote_ids
            q["is_saved"] = f"{q['poem_id']}|||{q['quote_text']}" in saved_quote_keys
            quotes_list.append(q)

    finally:
        db.close()

    return render_template("quotes.html", quotes=quotes_list)


@app.route("/toggle_quote_like/<int:quote_id>", methods=["POST"])
@login_required
def toggle_quote_like(quote_id):
    db = get_db()
    try:
        quote = db.execute("SELECT id FROM quotes WHERE id=%s", (quote_id,)).fetchone()
        if not quote:
            return jsonify({"success": False, "error": "Quote not found"}), 404

        existing = db.execute(
            "SELECT id FROM quote_likes WHERE user_id=%s AND quote_id=%s",
            (session["user_id"], quote_id)
        ).fetchone()

        if existing:
            db.execute("DELETE FROM quote_likes WHERE user_id=%s AND quote_id=%s",
                       (session["user_id"], quote_id))
            action = "unliked"
        else:
            db.execute(
                "INSERT INTO quote_likes (user_id, quote_id, date) VALUES (%s, %s, %s)",
                (session["user_id"], quote_id, datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
            )
            action = "liked"

        like_count = db.execute(
            "SELECT COUNT(*) FROM quote_likes WHERE quote_id=%s", (quote_id,)
        ).fetchone()[0]
        db.execute("UPDATE quotes SET likes=%s WHERE id=%s", (like_count, quote_id))
        db.commit()
    finally:
        db.close()

    return jsonify({"success": True, "action": action, "like_count": like_count})


@app.route("/toggle_save_shared_quote/<int:quote_id>", methods=["POST"])
@login_required
def toggle_save_shared_quote(quote_id):
    db = get_db()
    try:
        quote = db.execute(
            "SELECT id, poem_id, quote_text FROM quotes WHERE id=%s", (quote_id,)
        ).fetchone()
        if not quote:
            return jsonify({"success": False, "error": "Quote not found"}), 404

        existing = db.execute(
            "SELECT id FROM saved_quotes WHERE user_id=%s AND poem_id=%s AND quote_text=%s",
            (session["user_id"], quote["poem_id"], quote["quote_text"])
        ).fetchone()

        if existing:
            db.execute(
                "DELETE FROM saved_quotes WHERE user_id=%s AND poem_id=%s AND quote_text=%s",
                (session["user_id"], quote["poem_id"], quote["quote_text"])
            )
            action = "unsaved"
        else:
            db.execute(
                "INSERT INTO saved_quotes (user_id, poem_id, quote_text, date) VALUES (%s, %s, %s, %s)",
                (session["user_id"], quote["poem_id"], quote["quote_text"],
                 datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
            )
            action = "saved"
        check_and_award_badges(db, session["user_id"])
        db.commit()
    finally:
        db.close()

    return jsonify({"success": True, "action": action})


@app.route("/share_quote", methods=["POST"])
@login_required
def share_quote():
    poem_id    = request.form.get("poem_id")
    quote_text = request.form.get("quote_text", "").strip()
    description= request.form.get("description", "").strip()

    if not poem_id or not quote_text:
        return jsonify({"success": False, "error": "Missing poem_id or quote_text"}), 400

    db = get_db()
    try:
        poem = db.execute(
            "SELECT id, title, author FROM poems WHERE id=%s AND status='published'", (poem_id,)
        ).fetchone()
        if not poem:
            return jsonify({"success": False, "error": "Poem not found"}), 404

        db.execute("""
            INSERT INTO quotes (user_id, poem_id, author, poem_title, quote_text, description, type, date)
            VALUES (%s, %s, %s, %s, %s, %s, 'poem', %s)
        """, (
            session["user_id"], poem["id"], poem["author"], poem["title"],
            quote_text, description, datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        ))
        check_and_award_badges(db, session["user_id"])
        db.commit()
    finally:
        db.close()

    return jsonify({"success": True})


# ============================================================
# BADGES
# ============================================================
def award_badge(db, user_id, badge_name):
    badge = db.execute(
        "SELECT id FROM badges WHERE name=%s",
        (badge_name,)
    ).fetchone()

    if not badge:
        return

    db.execute(
        "INSERT INTO user_badges (user_id, badge_id) VALUES (%s, %s) ON CONFLICT (user_id, badge_id) DO NOTHING",
        (user_id, badge["id"])
    )


def get_user_badge_progress(db, user_id):
    progress = {}

    table_names = {
        r["table_name"] for r in
        db.execute("SELECT table_name FROM information_schema.tables WHERE table_schema='public'").fetchall()
    }

    progress["poems_published"] = db.execute(
        "SELECT COUNT(*) FROM poems WHERE user_id=%s AND status='published'", (user_id,)
    ).fetchone()[0]

    if "poem_likes" in table_names:
        progress["single_poem_likes"] = db.execute("""
            SELECT COALESCE(MAX(lc), 0) FROM (
                SELECT COUNT(pl.id) as lc FROM poems p
                LEFT JOIN poem_likes pl ON p.id=pl.poem_id
                WHERE p.user_id=%s AND p.status='published'
                GROUP BY p.id
            )
        """, (user_id,)).fetchone()[0] or 0

        progress["total_poem_likes"] = db.execute("""
            SELECT COUNT(pl.id) FROM poems p
            LEFT JOIN poem_likes pl ON p.id=pl.poem_id
            WHERE p.user_id=%s AND p.status='published'
        """, (user_id,)).fetchone()[0] or 0
    else:
        progress["single_poem_likes"] = 0
        progress["total_poem_likes"] = 0

    progress["quotes_shared"] = db.execute(
        "SELECT COUNT(*) FROM quotes WHERE user_id=%s", (user_id,)
    ).fetchone()[0]

    progress["single_quote_likes"] = db.execute(
        "SELECT COALESCE(MAX(likes), 0) FROM quotes WHERE user_id=%s", (user_id,)
    ).fetchone()[0] or 0

    quote_cols = {c["column_name"] for c in db.execute("""
        SELECT column_name FROM information_schema.columns
        WHERE table_schema='public' AND table_name='quotes'
    """).fetchall()}
    if "author" in quote_cols:
        progress["different_poets_quoted"] = db.execute("""
            SELECT COUNT(DISTINCT author) FROM quotes
            WHERE user_id=%s AND author IS NOT NULL AND TRIM(author) != ''
        """, (user_id,)).fetchone()[0] or 0
    else:
        progress["different_poets_quoted"] = 0

    progress["comments_written"] = db.execute(
        "SELECT COUNT(*) FROM comments WHERE user_id=%s AND deleted=0", (user_id,)
    ).fetchone()[0]

    progress["comment_replies"] = db.execute(
        "SELECT COUNT(*) FROM comments WHERE user_id=%s AND parent_id IS NOT NULL AND deleted=0",
        (user_id,)
    ).fetchone()[0]

    progress["long_comments"] = db.execute(
        "SELECT COUNT(*) FROM comments WHERE user_id=%s AND deleted=0 AND LENGTH(TRIM(comment))>=150",
        (user_id,)
    ).fetchone()[0]

    total_saves = 0
    if "saved_poems" in table_names:
        total_saves += db.execute(
            "SELECT COUNT(*) FROM saved_poems WHERE user_id=%s", (user_id,)
        ).fetchone()[0]
    if "saved_quotes" in table_names:
        total_saves += db.execute(
            "SELECT COUNT(*) FROM saved_quotes WHERE user_id=%s", (user_id,)
        ).fetchone()[0]
    progress["total_saves"] = total_saves

    if "followers" in table_names:
        progress["following_count"] = db.execute(
            "SELECT COUNT(*) FROM followers WHERE follower_id=%s", (user_id,)
        ).fetchone()[0]
        progress["followers_count"] = db.execute(
            "SELECT COUNT(*) FROM followers WHERE following_id=%s", (user_id,)
        ).fetchone()[0]
    else:
        progress["following_count"] = 0
        progress["followers_count"] = 0

    if "duels" in table_names:
        progress["duel_wins"] = db.execute(
            "SELECT COUNT(*) FROM duels WHERE winner_id=%s AND status='closed'", (user_id,)
        ).fetchone()[0]
    else:
        progress["duel_wins"] = 0

    if "daily_game_streaks" in table_names:
        row = db.execute(
            "SELECT total_correct FROM daily_game_streaks WHERE user_id=%s", (user_id,)
        ).fetchone()
        progress["game_correct_guesses"] = row["total_correct"] if row else 0
    else:
        progress["game_correct_guesses"] = 0

    return progress


def check_and_award_badges(db, user_id):
    all_badges = db.execute("SELECT * FROM badges").fetchall()
    progress = get_user_badge_progress(db, user_id)

    for badge in all_badges:
        if badge["condition_type"] == "manual":
            continue

        current = progress.get(badge["condition_type"], 0)
        if badge["condition_value"] is not None and current >= badge["condition_value"]:
            award_badge(db, user_id, badge["name"])


@app.route("/badges")
@login_required
def badges_page():
    user_id = session["user_id"]
    db = get_db()
    try:
        check_and_award_badges(db, user_id)

        badges = db.execute("""
            SELECT b.id, b.name, b.description, b.icon, b.category,
                b.condition_type, b.condition_value,
                ub.date_awarded, ub.is_featured,
                CASE WHEN ub.id IS NOT NULL THEN 1 ELSE 0 END AS earned
            FROM badges b
            LEFT JOIN user_badges ub ON b.id=ub.badge_id AND ub.user_id=%s
            ORDER BY
                CASE b.category
                    WHEN 'Author Badges'           THEN 1
                    WHEN 'Quote Badges'            THEN 2
                    WHEN 'Community Badges'        THEN 3
                    WHEN 'Reader Badges'           THEN 4
                    WHEN 'Social Badges'           THEN 5
                    WHEN 'Special Platform Badges' THEN 6
                    ELSE 7
                END, b.id
        """, (user_id,)).fetchall()

        featured_badges = db.execute("""
            SELECT b.id, b.name, b.icon
            FROM user_badges ub
            JOIN badges b ON ub.badge_id=b.id
            WHERE ub.user_id=%s AND ub.is_featured=1
            ORDER BY ub.date_awarded DESC
            LIMIT 3
        """, (user_id,)).fetchall()

        db.commit()
    finally:
        db.close()

    grouped_badges = defaultdict(list)
    for badge in badges:
        grouped_badges[badge["category"]].append(badge)

    return render_template(
        "badges.html",
        grouped_badges=dict(grouped_badges),
        featured_badges=featured_badges
    )


@app.route("/badges/feature/<int:badge_id>", methods=["POST"])
@login_required
def feature_badge(badge_id):
    user_id = session["user_id"]
    db = get_db()
    try:
        if not db.execute(
            "SELECT 1 FROM user_badges WHERE user_id=%s AND badge_id=%s", (user_id, badge_id)
        ).fetchone():
            flash("You can only feature earned badges.", "error")
            return redirect(url_for("badges_page"))

        if db.execute(
            "SELECT 1 FROM user_badges WHERE user_id=%s AND badge_id=%s AND is_featured=1",
            (user_id, badge_id)
        ).fetchone():
            flash("Badge is already featured.", "info")
            return redirect(url_for("badges_page"))

        count = db.execute(
            "SELECT COUNT(*) FROM user_badges WHERE user_id=%s AND is_featured=1", (user_id,)
        ).fetchone()[0]
        if count >= 3:
            flash("You can feature at most 3 badges.", "error")
            return redirect(url_for("badges_page"))

        db.execute(
            "UPDATE user_badges SET is_featured=1 WHERE user_id=%s AND badge_id=%s",
            (user_id, badge_id)
        )
        db.commit()
    finally:
        db.close()

    flash("Badge added to your salon.", "success")
    return redirect(url_for("badges_page"))


@app.route("/badges/unfeature/<int:badge_id>", methods=["POST"])
@login_required
def unfeature_badge(badge_id):
    user_id = session["user_id"]
    db = get_db()
    try:
        db.execute(
            "UPDATE user_badges SET is_featured=0 WHERE user_id=%s AND badge_id=%s",
            (user_id, badge_id)
        )
        db.commit()
    finally:
        db.close()

    flash("Badge removed from your salon.", "success")
    return redirect(url_for("badges_page"))



# ============================================================
# SAVE PUBLIC KEY
# ============================================================
@app.route("/messages/save_public_key", methods=["POST"])
@login_required
def save_public_key():
    data = request.get_json(silent=True)

    if not data:
        return jsonify({"success": False, "error": "No JSON received"}), 400

    public_key = (data.get("public_key") or "").strip()

    if not public_key:
        return jsonify({"success": False, "error": "Missing public key"}), 400

    db = get_db()
    try:
        db.execute(
            "UPDATE users SET public_key=%s WHERE id=%s",
            (public_key, session["user_id"])
        )
        db.commit()
    finally:
        db.close()

    return jsonify({"success": True})


# ============================================================
# GET USER PUBLIC KEY
# ============================================================
@app.route("/messages/public_key/<int:user_id>", methods=["GET"])
@login_required
def get_user_public_key(user_id):
    db = get_db()
    try:
        user = db.execute(
            "SELECT id, public_key FROM users WHERE id=%s",
            (user_id,)
        ).fetchone()
    finally:
        db.close()

    if not user:
        return jsonify({"success": False, "error": "User not found"}), 404

    if not user["public_key"]:
        return jsonify({"success": False, "error": "User has no public key"}), 404

    return jsonify({
        "success": True,
        "user_id": user["id"],
        "public_key": user["public_key"]
    })


# ============================================================
# SEND MESSAGE
# ============================================================
@app.route("/messages/send", methods=["POST"])
@login_required
def send_message():
    data = request.get_json(silent=True)

    if not data:
        return jsonify({"success": False, "error": "Invalid JSON request"}), 400

    receiver_id = data.get("receiver_id")
    encrypted_message = (data.get("encrypted_message") or "").strip()
    encrypted_key_sender = (data.get("encrypted_key_sender") or "").strip()
    encrypted_key_receiver = (data.get("encrypted_key_receiver") or "").strip()
    iv = (data.get("iv") or "").strip()
    sender_id = session["user_id"]

    missing_fields = []
    if not receiver_id:
        missing_fields.append("receiver_id")
    if not encrypted_message:
        missing_fields.append("encrypted_message")
    if not encrypted_key_sender:
        missing_fields.append("encrypted_key_sender")
    if not encrypted_key_receiver:
        missing_fields.append("encrypted_key_receiver")
    if not iv:
        missing_fields.append("iv")

    if missing_fields:
        return jsonify({"success": False, "error": "Missing data", "missing_fields": missing_fields}), 400

    try:
        receiver_id = int(receiver_id)
    except (ValueError, TypeError):
        return jsonify({"success": False, "error": "Invalid receiver"}), 400

    if sender_id == receiver_id:
        return jsonify({"success": False, "error": "Cannot message yourself"}), 400

    db = get_db()
    try:
        receiver = db.execute(
            "SELECT id FROM users WHERE id=%s",
            (receiver_id,)
        ).fetchone()

        if not receiver:
            return jsonify({"success": False, "error": "Receiver not found"}), 404

        conv = db.execute("""
            SELECT * FROM conversations
            WHERE (user1_id=%s AND user2_id=%s) OR (user1_id=%s AND user2_id=%s)
            LIMIT 1
        """, (sender_id, receiver_id, receiver_id, sender_id)).fetchone()

        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        if not conv:
            db.execute(
                "INSERT INTO conversations (user1_id, user2_id, created_at) VALUES (%s, %s, %s) ON CONFLICT (user1_id, user2_id) DO NOTHING",
                (sender_id, receiver_id, now_str)
            )
            db.commit()
            conv = db.execute("""
                SELECT * FROM conversations
                WHERE (user1_id=%s AND user2_id=%s) OR (user1_id=%s AND user2_id=%s)
                LIMIT 1
            """, (sender_id, receiver_id, receiver_id, sender_id)).fetchone()

        conversation_id = conv["id"]

        db.execute("""
            INSERT INTO messages (
                conversation_id,
                sender_id,
                message,
                encrypted_message,
                encrypted_key_sender,
                encrypted_key_receiver,
                iv,
                is_read,
                date
            )
            VALUES (%s, %s, '', %s, %s, %s, %s, 0, %s)
        """, (
            conversation_id,
            sender_id,
            encrypted_message,
            encrypted_key_sender,
            encrypted_key_receiver,
            iv,
            now_str
        ))

        db.commit()
    finally:
        db.close()

    return jsonify({"success": True, "conversation_id": conversation_id})


# ============================================================
# GET MESSAGES
# ============================================================
@app.route("/messages/<int:other_user_id>", methods=["GET"])
@login_required
def get_messages(other_user_id):
    current_user = session["user_id"]
    db = get_db()

    try:
        conv = db.execute("""
            SELECT * FROM conversations
            WHERE (user1_id=%s AND user2_id=%s) OR (user1_id=%s AND user2_id=%s)
            LIMIT 1
        """, (current_user, other_user_id, other_user_id, current_user)).fetchone()

        if not conv:
            return jsonify({"messages": []})

        messages = db.execute("""
            SELECT m.*, u.username
            FROM messages m
            JOIN users u ON m.sender_id = u.id
            WHERE m.conversation_id=%s
            ORDER BY m.date ASC, m.id ASC
        """, (conv["id"],)).fetchall()

        db.execute("""
            UPDATE messages
            SET is_read=1
            WHERE conversation_id=%s AND sender_id!=%s
        """, (conv["id"], current_user))
        db.commit()

    finally:
        db.close()

    return jsonify({
        "messages": [
            {
                "id": m["id"],
                "sender_id": m["sender_id"],
                "username": m["username"],
                "encrypted_message": m["encrypted_message"],
                "encrypted_key_sender": m["encrypted_key_sender"] or m["encrypted_key"] or "",
                "encrypted_key_receiver": m["encrypted_key_receiver"] or m["encrypted_key"] or "",
                "iv": m["iv"],
                "date": m["date"]
            }
            for m in messages
        ]
    })


# ============================================================
# CONVERSATION LIST
# ============================================================
@app.route("/messages/conversations", methods=["GET"])
@login_required
def list_conversations():
    current_user = session["user_id"]
    db = get_db()

    try:
        rows = db.execute("""
            SELECT c.id AS conversation_id,
                   u.id AS other_user_id,
                   u.username,
                   u.profile_picture,
                   (SELECT COUNT(*)
                    FROM messages m
                    WHERE m.conversation_id = c.id
                      AND m.sender_id != %s
                      AND m.is_read = 0) AS unread_count
            FROM conversations c
            JOIN users u
              ON u.id = CASE
                  WHEN c.user1_id = %s THEN c.user2_id
                  ELSE c.user1_id
              END
            WHERE c.user1_id = %s OR c.user2_id = %s
            ORDER BY c.id DESC
        """, (current_user, current_user, current_user, current_user)).fetchall()
    finally:
        db.close()

    return jsonify({
        "conversations": [
            {
                "conversation_id": r["conversation_id"],
                "other_user_id": r["other_user_id"],
                "username": r["username"],
                "profile_picture": r["profile_picture"],
                "last_message": "",
                "unread_count": r["unread_count"]
            }
            for r in rows
        ]
    })




# ============================================================
# CHAIN POETRY SALON
# ============================================================
@app.route("/chain-salon")
def chain_salon():
    db = get_db()
    try:
        active = db.execute("""
            SELECT cp.*, u.username AS creator_name,
                   (SELECT COUNT(*) FROM chain_stanzas cs WHERE cs.chain_poem_id=cp.id) AS stanza_count
            FROM chain_poems cp
            JOIN users u ON cp.creator_id=u.id
            WHERE cp.status='active'
            ORDER BY cp.created_at DESC
        """).fetchall()
        ended = db.execute("""
            SELECT cp.*, u.username AS creator_name,
                   (SELECT COUNT(*) FROM chain_stanzas cs WHERE cs.chain_poem_id=cp.id) AS stanza_count
            FROM chain_poems cp
            JOIN users u ON cp.creator_id=u.id
            WHERE cp.status='ended'
            ORDER BY cp.ended_at DESC
        """).fetchall()
    finally:
        db.close()
    return render_template("chain_salon.html", active=active, ended=ended)


@app.route("/chain-salon/create", methods=["GET", "POST"])
@login_required
def chain_salon_create():
    if request.method == "POST":
        title        = request.form.get("title", "").strip()
        stanza_text  = request.form.get("stanza_text", "").strip()
        if not title or not stanza_text:
            return render_template("chain_salon_create.html", error="Title and first stanza are required.")
        db = get_db()
        try:
            now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            cur = db.execute(
                "INSERT INTO chain_poems (title, creator_id, status, created_at) VALUES (%s,%s,%s,%s) RETURNING id",
                (title, session["user_id"], "active", now)
            )
            poem_id = cur.fetchone()["id"]
            db.execute(
                "INSERT INTO chain_stanzas (chain_poem_id, user_id, stanza_text, position, created_at) VALUES (%s,%s,%s,1,%s)",
                (poem_id, session["user_id"], stanza_text, now)
            )
            db.commit()
        finally:
            db.close()
        return redirect(url_for("chain_salon_detail", poem_id=poem_id))
    return render_template("chain_salon_create.html")


@app.route("/chain-salon/<int:poem_id>")
def chain_salon_detail(poem_id):
    db = get_db()
    try:
        poem = db.execute("""
            SELECT cp.*, u.username AS creator_name
            FROM chain_poems cp JOIN users u ON cp.creator_id=u.id
            WHERE cp.id=%s
        """, (poem_id,)).fetchone()
        if not poem:
            abort(404)
        stanzas = db.execute("""
            SELECT cs.*, u.username
            FROM chain_stanzas cs JOIN users u ON cs.user_id=u.id
            WHERE cs.chain_poem_id=%s
            ORDER BY cs.position ASC
        """, (poem_id,)).fetchall()
        contributors = db.execute("""
            SELECT DISTINCT u.id, u.username
            FROM chain_stanzas cs JOIN users u ON cs.user_id=u.id
            WHERE cs.chain_poem_id=%s
        """, (poem_id,)).fetchall()
        last_user_id = stanzas[-1]["user_id"] if stanzas else None
    finally:
        db.close()
    return render_template("chain_salon_detail.html",
                           poem=poem, stanzas=stanzas,
                           contributors=contributors,
                           last_user_id=last_user_id)


@app.route("/chain-salon/<int:poem_id>/add", methods=["POST"])
@login_required
def chain_salon_add(poem_id):
    db = get_db()
    try:
        poem = db.execute("SELECT * FROM chain_poems WHERE id=%s", (poem_id,)).fetchone()
        if not poem or poem["status"] != "active":
            return jsonify({"success": False, "error": "Poem is not active"}), 400
        stanza_text = request.form.get("stanza_text", "").strip()
        if not stanza_text:
            return jsonify({"success": False, "error": "Stanza cannot be empty"}), 400
        last = db.execute(
            "SELECT user_id FROM chain_stanzas WHERE chain_poem_id=%s ORDER BY position DESC LIMIT 1",
            (poem_id,)
        ).fetchone()
        if last and last["user_id"] == session["user_id"]:
            return jsonify({"success": False, "error": "You cannot add two consecutive stanzas"}), 403
        next_pos = db.execute(
            "SELECT COALESCE(MAX(position),0)+1 FROM chain_stanzas WHERE chain_poem_id=%s", (poem_id,)
        ).fetchone()[0]
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        db.execute(
            "INSERT INTO chain_stanzas (chain_poem_id, user_id, stanza_text, position, created_at) VALUES (%s,%s,%s,%s,%s)",
            (poem_id, session["user_id"], stanza_text, next_pos, now)
        )
        db.commit()
    finally:
        db.close()
    return redirect(url_for("chain_salon_detail", poem_id=poem_id))


@app.route("/chain-salon/<int:poem_id>/end", methods=["POST"])
@login_required
def chain_salon_end(poem_id):
    db = get_db()
    try:
        poem = db.execute("SELECT * FROM chain_poems WHERE id=%s", (poem_id,)).fetchone()
        if not poem:
            abort(404)
        if poem["creator_id"] != session["user_id"]:
            abort(403)
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        db.execute("UPDATE chain_poems SET status='ended', ended_at=%s WHERE id=%s", (now, poem_id))
        db.commit()
    finally:
        db.close()
    return redirect(url_for("chain_salon_detail", poem_id=poem_id))


# ============================================================
# WEEKLY THEME SALON
# ============================================================
def _get_active_theme(db):
    today = datetime.now().strftime("%Y-%m-%d")
    return db.execute(
        "SELECT * FROM weekly_themes WHERE start_date<=%s AND end_date>=%s ORDER BY id DESC LIMIT 1",
        (today, today)
    ).fetchone()

# ============================================================
# WEEKLY SALON + DUEL AUTO-CLOSE HELPERS
# ============================================================

DUEL_DURATION_HOURS = 24


def _parse_dt(dt_value):
    if not dt_value:
        return None
    if isinstance(dt_value, datetime):
        return dt_value

    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(dt_value, fmt)
        except ValueError:
            continue
    return None


def _close_duel_and_set_winner(db, duel):
    challenger_votes = db.execute(
        "SELECT COUNT(*) FROM duel_votes WHERE duel_id=%s AND voted_for=%s",
        (duel["id"], duel["challenger_id"])
    ).fetchone()[0]

    challenged_votes = db.execute(
        "SELECT COUNT(*) FROM duel_votes WHERE duel_id=%s AND voted_for=%s",
        (duel["id"], duel["challenged_id"])
    ).fetchone()[0]

    if challenger_votes > challenged_votes:
        winner_id = duel["challenger_id"]
    elif challenged_votes > challenger_votes:
        winner_id = duel["challenged_id"]
    else:
        winner_id = None

    db.execute(
        "UPDATE duels SET status='closed', winner_id=%s WHERE id=%s",
        (winner_id, duel["id"])
    )

    if winner_id:
        check_and_award_badges(db, winner_id)

    return winner_id


def _auto_close_expired_duel(db, duel):
    """
    Closes duel automatically if 24 hours passed from created_at.
    Returns the updated duel row.
    """
    if not duel:
        return None

    if duel["status"] == "closed":
        return duel

    created_at = _parse_dt(duel["created_at"])
    if not created_at:
        return duel

    expires_at = created_at + timedelta(hours=DUEL_DURATION_HOURS)
    now = datetime.now()

    if now >= expires_at:
        _close_duel_and_set_winner(db, duel)
        db.commit()
        duel = db.execute("SELECT * FROM duels WHERE id=%s", (duel["id"],)).fetchone()

    return duel


def _auto_close_expired_duels_for_theme(db, theme_id):
    """
    Closes all expired duels for a given theme.
    """
    duels = db.execute("""
        SELECT *
        FROM duels
        WHERE theme_id=%s AND status IN ('pending', 'active')
    """, (theme_id,)).fetchall()

    changed = False
    now = datetime.now()

    for duel in duels:
        created_at = _parse_dt(duel["created_at"])
        if not created_at:
            continue

        expires_at = created_at + timedelta(hours=DUEL_DURATION_HOURS)
        if now >= expires_at:
            _close_duel_and_set_winner(db, duel)
            changed = True

    if changed:
        db.commit()


def _get_duel_time_left_seconds(duel):
    if not duel or duel["status"] == "closed":
        return 0

    created_at = _parse_dt(duel["created_at"])
    if not created_at:
        return 0

    expires_at = created_at + timedelta(hours=DUEL_DURATION_HOURS)
    diff = expires_at - datetime.now()
    return max(0, int(diff.total_seconds()))


@app.route("/weekly-salon")
def weekly_salon():
    db = get_db()
    try:
        theme = _get_active_theme(db)
        entries = []
        user_submitted = False
        duels = []

        if theme:
            # auto-close expired duels for this theme first
            _auto_close_expired_duels_for_theme(db, theme["id"])

            entries = db.execute("""
                SELECT wtp.*, u.username
                FROM weekly_theme_poems wtp
                JOIN users u ON wtp.user_id = u.id
                WHERE wtp.theme_id=%s
                ORDER BY wtp.submitted_at DESC
            """, (theme["id"],)).fetchall()

            if "user_id" in session:
                user_submitted = bool(db.execute(
                    "SELECT 1 FROM weekly_theme_poems WHERE theme_id=%s AND user_id=%s",
                    (theme["id"], session["user_id"])
                ).fetchone())

            duels_raw = db.execute("""
                SELECT d.*,
                       u1.username AS challenger_name,
                       u2.username AS challenged_name
                FROM duels d
                JOIN users u1 ON d.challenger_id = u1.id
                JOIN users u2 ON d.challenged_id = u2.id
                WHERE d.theme_id=%s AND d.status IN ('active', 'pending', 'closed')
                ORDER BY d.created_at DESC
            """, (theme["id"],)).fetchall()

            duels = []
            for duel in duels_raw:
                duel_dict = dict(duel)
                duel_dict["time_left"] = _get_duel_time_left_seconds(duel)
                duels.append(duel_dict)

        past_themes = db.execute(
            "SELECT * FROM weekly_themes WHERE end_date<%s ORDER BY end_date DESC",
            (datetime.now().strftime("%Y-%m-%d"),)
        ).fetchall()

    finally:
        db.close()

    now = datetime.now()
    time_left = None
    if theme:
        end_dt = datetime.strptime(theme["end_date"], "%Y-%m-%d")
        diff = end_dt - now
        time_left = max(0, int(diff.total_seconds()))

    return render_template(
        "weekly_salon.html",
        theme=theme,
        entries=entries,
        user_submitted=user_submitted,
        past_themes=past_themes,
        time_left=time_left,
        duels=duels
    )

@app.route("/weekly-salon/submit", methods=["POST"])
@login_required
def weekly_salon_submit():
    db = get_db()
    try:
        theme = _get_active_theme(db)
        if not theme:
            return redirect(url_for("weekly_salon"))

        already = db.execute(
            "SELECT 1 FROM weekly_theme_poems WHERE theme_id=%s AND user_id=%s",
            (theme["id"], session["user_id"])
        ).fetchone()
        if already:
            return redirect(url_for("weekly_salon"))

        title = request.form.get("title", "").strip()
        text = request.form.get("text", "").strip()

        if not title or not text:
            return redirect(url_for("weekly_salon"))

        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        db.execute(
            "INSERT INTO weekly_theme_poems (theme_id, user_id, title, text, submitted_at) VALUES (%s,%s,%s,%s,%s)",
            (theme["id"], session["user_id"], title, text, now)
        )
        db.commit()
    finally:
        db.close()

    return redirect(url_for("weekly_salon"))


@app.route("/weekly-salon/archive")
def weekly_salon_archive():
    db = get_db()
    try:
        today = datetime.now().strftime("%Y-%m-%d")
        themes = db.execute(
            "SELECT * FROM weekly_themes WHERE end_date<%s ORDER BY end_date DESC",
            (today,)
        ).fetchall()

        archive = {}
        for t in themes:
            poems = db.execute("""
                SELECT wtp.*, u.username
                FROM weekly_theme_poems wtp
                JOIN users u ON wtp.user_id=u.id
                WHERE wtp.theme_id=%s
                ORDER BY wtp.submitted_at
            """, (t["id"],)).fetchall()

            archive[t["id"]] = {
                "theme": t,
                "poems": poems
            }
    finally:
        db.close()

    return render_template("weekly_salon_archive.html", archive=archive)


@app.route("/weekly-salon/theme/new", methods=["POST"])
@login_required
def weekly_salon_new_theme():
    if not session.get("is_admin"):
        abort(403)

    title = request.form.get("title", "").strip()
    start_date = request.form.get("start_date", "").strip()
    end_date = request.form.get("end_date", "").strip()

    if not title or not start_date or not end_date:
        return redirect(url_for("weekly_salon"))

    db = get_db()
    try:
        db.execute(
            "INSERT INTO weekly_themes (title, start_date, end_date, created_by) VALUES (%s,%s,%s,%s)",
            (title, start_date, end_date, session["user_id"])
        )
        db.commit()
    finally:
        db.close()

    return redirect(url_for("weekly_salon"))


# ============================================================
# POET DUELS
# ============================================================

@app.route("/duels/challenge", methods=["POST"])
@login_required
def duel_challenge():
    db = get_db()
    try:
        theme = _get_active_theme(db)
        if not theme:
            return jsonify({"success": False, "error": "No active theme"}), 400

        challenged_id = request.form.get("challenged_id", type=int)
        if not challenged_id or challenged_id == session["user_id"]:
            return jsonify({"success": False, "error": "Invalid challenge target"}), 400

        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        cur = db.execute("""
            INSERT INTO duels (theme_id, challenger_id, challenged_id, status, created_at)
            VALUES (%s, %s, %s, 'pending', %s)
            RETURNING id
        """, (theme["id"], session["user_id"], challenged_id, now))
        db.commit()

        create_notification(
            challenged_id,
            f"{session['username']} challenged you to a duel for theme: {theme['title']}!"
        )

        duel_id = cur.fetchone()["id"]
    finally:
        db.close()

    return redirect(url_for("duel_detail", duel_id=duel_id))


@app.route("/duels/<int:duel_id>/accept", methods=["POST"])
@login_required
def duel_accept(duel_id):
    db = get_db()
    try:
        duel = db.execute("SELECT * FROM duels WHERE id=%s", (duel_id,)).fetchone()
        duel = _auto_close_expired_duel(db, duel)

        if not duel or duel["challenged_id"] != session["user_id"]:
            abort(403)

        if duel["status"] == "closed":
            return redirect(url_for("duel_detail", duel_id=duel_id))

        if duel["status"] != "pending":
            return redirect(url_for("duel_detail", duel_id=duel_id))

        db.execute("UPDATE duels SET status='active' WHERE id=%s", (duel_id,))
        db.commit()
    finally:
        db.close()

    return redirect(url_for("duel_detail", duel_id=duel_id))


@app.route("/duels/<int:duel_id>/vote", methods=["POST"])
@login_required
def duel_vote(duel_id):
    db = get_db()
    try:
        duel = db.execute("SELECT * FROM duels WHERE id=%s", (duel_id,)).fetchone()
        duel = _auto_close_expired_duel(db, duel)

        if not duel or duel["status"] != "active":
            return jsonify({"success": False, "error": "Duel not open for voting"}), 400

        if session["user_id"] in (duel["challenger_id"], duel["challenged_id"]):
            return jsonify({"success": False, "error": "Participants cannot vote"}), 403

        voted_for = request.form.get("voted_for", type=int)
        if voted_for not in (duel["challenger_id"], duel["challenged_id"]):
            return jsonify({"success": False, "error": "Invalid vote target"}), 400

        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        try:
            db.execute("""
                INSERT INTO duel_votes (duel_id, voter_id, voted_for, voted_at)
                VALUES (%s,%s,%s,%s)
            """, (duel_id, session["user_id"], voted_for, now))
            db.commit()
        except Exception:
            return jsonify({"success": False, "error": "Already voted"}), 400
    finally:
        db.close()

    return redirect(url_for("duel_detail", duel_id=duel_id))



@app.route("/duels/<int:duel_id>")
def duel_detail(duel_id):
    db = get_db()
    try:
        duel = db.execute("""
            SELECT d.*,
                   u1.username AS challenger_name,
                   u2.username AS challenged_name,
                   t.title AS theme_title
            FROM duels d
            JOIN users u1 ON d.challenger_id = u1.id
            JOIN users u2 ON d.challenged_id = u2.id
            JOIN weekly_themes t ON d.theme_id = t.id
            WHERE d.id=%s
        """, (duel_id,)).fetchone()

        if not duel:
            abort(404)

        duel = _auto_close_expired_duel(db, duel)

        # re-read full joined duel row if it was updated
        duel = db.execute("""
            SELECT d.*,
                   u1.username AS challenger_name,
                   u2.username AS challenged_name,
                   t.title AS theme_title
            FROM duels d
            JOIN users u1 ON d.challenger_id = u1.id
            JOIN users u2 ON d.challenged_id = u2.id
            JOIN weekly_themes t ON d.theme_id = t.id
            WHERE d.id=%s
        """, (duel_id,)).fetchone()

        challenger_poem = db.execute(
            "SELECT * FROM weekly_theme_poems WHERE theme_id=%s AND user_id=%s",
            (duel["theme_id"], duel["challenger_id"])
        ).fetchone()

        challenged_poem = db.execute(
            "SELECT * FROM weekly_theme_poems WHERE theme_id=%s AND user_id=%s",
            (duel["theme_id"], duel["challenged_id"])
        ).fetchone()

        challenger_votes = db.execute(
            "SELECT COUNT(*) FROM duel_votes WHERE duel_id=%s AND voted_for=%s",
            (duel_id, duel["challenger_id"])
        ).fetchone()[0]

        challenged_votes = db.execute(
            "SELECT COUNT(*) FROM duel_votes WHERE duel_id=%s AND voted_for=%s",
            (duel_id, duel["challenged_id"])
        ).fetchone()[0]

        user_vote = None
        if "user_id" in session:
            row = db.execute(
                "SELECT voted_for FROM duel_votes WHERE duel_id=%s AND voter_id=%s",
                (duel_id, session["user_id"])
            ).fetchone()
            user_vote = row["voted_for"] if row else None

        duel_time_left = _get_duel_time_left_seconds(duel)

    finally:
        db.close()

    return render_template(
        "duel_detail.html",
        duel=duel,
        challenger_poem=challenger_poem,
        challenged_poem=challenged_poem,
        challenger_votes=challenger_votes,
        challenged_votes=challenged_votes,
        user_vote=user_vote,
        duel_time_left=duel_time_left
    )


# ============================================================
# HIDDEN POET GAME
# ============================================================
def _get_today_questions(db):
    today = datetime.now().strftime("%Y-%m-%d")
    return db.execute(
        "SELECT * FROM daily_game_questions WHERE game_date=%s ORDER BY position ASC",
        (today,)
    ).fetchall()


def _ensure_today_questions(db):
    """Auto-generate today's questions from random published poems if not set."""
    today = datetime.now().strftime("%Y-%m-%d")

    existing = db.execute(
        "SELECT COUNT(*) FROM daily_game_questions WHERE game_date=%s", (today,)
    ).fetchone()[0]

    if existing >= 3:
        return

    poems = db.execute(
        "SELECT id, text, author, title FROM poems WHERE status='published' ORDER BY RANDOM() LIMIT 3"
    ).fetchall()

    for i, poem in enumerate(poems, 1):

        lines = [l.strip() for l in poem["text"].split("\n") if l.strip()]

        if len(lines) <= 6:
            stanza = "\n".join(lines)

        else:
            start = random.randint(0, len(lines) - 6)
            stanza = "\n".join(lines[start:start+6])

        db.execute(
            """
            INSERT INTO daily_game_questions
            (game_date, poem_id, stanza_text, position)
            VALUES (%s,%s,%s,%s)
            ON CONFLICT (game_date, position) DO NOTHING
            """,
            (today, poem["id"], stanza, i)
        )

    db.commit()


@app.route("/hidden-poet")
def hidden_poet():
    db = get_db()
    try:
        _ensure_today_questions(db)
        questions = _get_today_questions(db)
        today = datetime.now().strftime("%Y-%m-%d")
        attempts = {}
        streak = None
        if "user_id" in session:
            rows = db.execute(
                "SELECT * FROM daily_game_attempts WHERE user_id=%s AND game_date=%s",
                (session["user_id"], today)
            ).fetchall()
            attempts = {r["question_pos"]: dict(r) for r in rows}
            streak = db.execute(
                "SELECT * FROM daily_game_streaks WHERE user_id=%s", (session["user_id"],)
            ).fetchone()
        leaderboard = db.execute("""
            SELECT u.username, s.total_correct, s.current_streak, s.best_streak
            FROM daily_game_streaks s JOIN users u ON s.user_id=u.id
            ORDER BY s.total_correct DESC LIMIT 10
        """).fetchall()
        # Reveal answers for already attempted questions
        answered_poems = {}
        for pos, attempt in attempts.items():
            q = next((q for q in questions if q["position"] == pos), None)
            if q:
                poem = db.execute("SELECT * FROM poems WHERE id=%s", (q["poem_id"],)).fetchone()
                if poem:
                    answered_poems[pos] = poem
    finally:
        db.close()
    return render_template("hidden_poet.html",
                           questions=questions, attempts=attempts,
                           answered_poems=answered_poems,
                           streak=streak, leaderboard=leaderboard,
                           today=datetime.now().strftime("%Y-%m-%d"))


@app.route("/hidden-poet/answer", methods=["POST"])
@login_required
def hidden_poet_answer():
    db = get_db()
    try:
        today = datetime.now().strftime("%Y-%m-%d")
        question_pos = request.form.get("question_pos", type=int)
        author_guess = request.form.get("author_guess", "").strip().lower()
        title_guess  = request.form.get("title_guess", "").strip().lower()

        question = db.execute(
            "SELECT * FROM daily_game_questions WHERE game_date=%s AND position=%s",
            (today, question_pos)
        ).fetchone()
        if not question:
            return redirect(url_for("hidden_poet"))

        already = db.execute(
            "SELECT 1 FROM daily_game_attempts WHERE user_id=%s AND game_date=%s AND question_pos=%s",
            (session["user_id"], today, question_pos)
        ).fetchone()
        if already:
            return redirect(url_for("hidden_poet"))

        poem = db.execute("SELECT * FROM poems WHERE id=%s", (question["poem_id"],)).fetchone()
        author_correct = 1 if author_guess and author_guess in poem["author"].lower() else 0
        title_correct  = 1 if title_guess  and title_guess  in poem["title"].lower()  else 0

        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        db.execute("""
            INSERT INTO daily_game_attempts
            (user_id, game_date, question_pos, author_guess, title_guess, author_correct, title_correct, attempted_at)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
            ON CONFLICT (user_id, game_date, question_pos) DO NOTHING
        """, (session["user_id"], today, question_pos, author_guess, title_guess,
              author_correct, title_correct, now))

        # Update streak row
        streak = db.execute(
            "SELECT * FROM daily_game_streaks WHERE user_id=%s", (session["user_id"],)
        ).fetchone()
        correct_inc = author_correct + title_correct
        if not streak:
            db.execute("""
                INSERT INTO daily_game_streaks (user_id, current_streak, best_streak, last_played, total_correct, total_guesses)
                VALUES (%s,1,1,%s,%s,2)
            """, (session["user_id"], today, correct_inc))
        else:
            last = streak["last_played"]
            yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
            new_streak = streak["current_streak"] + 1 if last == yesterday else (1 if last != today else streak["current_streak"])
            best = max(streak["best_streak"], new_streak)
            db.execute("""
                UPDATE daily_game_streaks SET current_streak=%s, best_streak=%s, last_played=%s,
                total_correct=total_correct+%s, total_guesses=total_guesses+2
                WHERE user_id=%s
            """, (new_streak, best, today, correct_inc, session["user_id"]))
        check_and_award_badges(db, session["user_id"])
        db.commit()
    finally:
        db.close()
    return redirect(url_for("hidden_poet"))


@app.route("/hidden-poet/questions/set", methods=["POST"])
@login_required
def hidden_poet_set_questions():
    if not session.get("is_admin"):
        abort(403)
    db = get_db()
    try:
        game_date = request.form.get("game_date", datetime.now().strftime("%Y-%m-%d"))
        db.execute("DELETE FROM daily_game_questions WHERE game_date=%s", (game_date,))
        for pos in range(1, 4):
            poem_id     = request.form.get(f"poem_id_{pos}", type=int)
            stanza_text = request.form.get(f"stanza_{pos}", "").strip()
            if poem_id and stanza_text:
                db.execute(
                    "INSERT INTO daily_game_questions (game_date, poem_id, stanza_text, position) VALUES (%s,%s,%s,%s)",
                    (game_date, poem_id, stanza_text, pos)
                )
        db.commit()
    finally:
        db.close()
    return redirect(url_for("hidden_poet"))


# ============================================================
# SALON CIRCLES
# ============================================================
def _get_circle_role(db, circle_id, user_id):
    row = db.execute(
        "SELECT role FROM circle_members WHERE circle_id=%s AND user_id=%s",
        (circle_id, user_id)
    ).fetchone()
    return row["role"] if row else None


@app.route("/circles")
def circles():
    db = get_db()
    try:
        all_circles = db.execute("""
            SELECT sc.*, u.username AS owner_name,
                   (SELECT COUNT(*) FROM circle_members cm WHERE cm.circle_id=sc.id) AS member_count
            FROM salon_circles sc JOIN users u ON sc.owner_id=u.id
            ORDER BY sc.created_at DESC
        """).fetchall()
        my_circles = []
        if "user_id" in session:
            my_circles = db.execute("""
                SELECT sc.*, cm.role,
                       (SELECT COUNT(*) FROM circle_members cm2 WHERE cm2.circle_id=sc.id) AS member_count
                FROM circle_members cm JOIN salon_circles sc ON cm.circle_id=sc.id
                WHERE cm.user_id=%s ORDER BY cm.joined_at DESC
            """, (session["user_id"],)).fetchall()
    finally:
        db.close()
    return render_template("circles.html", all_circles=all_circles, my_circles=my_circles)


@app.route("/circles/create", methods=["GET", "POST"])
@login_required
def circles_create():
    if request.method == "POST":
        name        = request.form.get("name", "").strip()
        description = request.form.get("description", "").strip()
        if not name:
            return render_template("circles_create.html", error="Circle name is required.")
        db = get_db()
        try:
            now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            cur = db.execute(
                "INSERT INTO salon_circles (name, description, owner_id, created_at) VALUES (%s,%s,%s,%s) RETURNING id",
                (name, description, session["user_id"], now)
            )
            circle_id = cur.fetchone()["id"]
            db.execute(
                "INSERT INTO circle_members (circle_id, user_id, role, joined_at) VALUES (%s,%s,%s,%s) ON CONFLICT (circle_id, user_id) DO NOTHING",
                (circle_id, session["user_id"], "owner", now)
            )
            db.commit()
        finally:
            db.close()
        return redirect(url_for("circle_detail", circle_id=circle_id))
    return render_template("circles_create.html")


@app.route("/circles/<int:circle_id>")
@login_required
def circle_detail(circle_id):
    db = get_db()
    try:
        circle = db.execute(
            "SELECT sc.*, u.username AS owner_name FROM salon_circles sc JOIN users u ON sc.owner_id=u.id WHERE sc.id=%s",
            (circle_id,)
        ).fetchone()
        if not circle:
            abort(404)
        role = _get_circle_role(db, circle_id, session["user_id"])
        if not role:
            abort(403)
        members = db.execute("""
            SELECT cm.*, u.username, u.profile_picture
            FROM circle_members cm JOIN users u ON cm.user_id=u.id
            WHERE cm.circle_id=%s ORDER BY cm.role, u.username
        """, (circle_id,)).fetchall()
        posts = db.execute("""
            SELECT cp.*, u.username,
                   p.title AS poem_title, p.text AS poem_text, p.author AS poem_author
            FROM circle_posts cp JOIN users u ON cp.user_id=u.id
            LEFT JOIN poems p ON cp.poem_id=p.id
            WHERE cp.circle_id=%s
            ORDER BY cp.is_pinned DESC, cp.posted_at DESC
        """, (circle_id,)).fetchall()
        messages = db.execute("""
            SELECT cm.*, u.username
            FROM circle_messages cm JOIN users u ON cm.user_id=u.id
            WHERE cm.circle_id=%s ORDER BY cm.sent_at ASC
        """, (circle_id,)).fetchall()
    finally:
        db.close()
    return render_template("circle_detail.html",
                           circle=circle, role=role,
                           members=members, posts=posts, messages=messages)


@app.route("/circles/<int:circle_id>/invite", methods=["POST"])
@login_required
def circle_invite(circle_id):
    db = get_db()
    try:
        role = _get_circle_role(db, circle_id, session["user_id"])
        if role not in ("owner", "admin"):
            abort(403)
        username = request.form.get("username", "").strip()
        user = db.execute("SELECT id FROM users WHERE username=%s", (username,)).fetchone()
        if not user:
            return redirect(url_for("circle_detail", circle_id=circle_id))
        already = _get_circle_role(db, circle_id, user["id"])
        if not already:
            now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            db.execute(
                "INSERT INTO circle_members (circle_id, user_id, role, joined_at) VALUES (%s,%s,%s,%s) ON CONFLICT (circle_id, user_id) DO NOTHING",
                (circle_id, user["id"], "member", now)
            )
            db.commit()
            create_notification(user["id"], f"You were invited to join the circle: {db.execute('SELECT name FROM salon_circles WHERE id=%s',(circle_id,)).fetchone()['name']}")
    finally:
        db.close()
    return redirect(url_for("circle_detail", circle_id=circle_id))

@app.route("/search_poems")
@login_required
def search_poems():
    q = request.args.get("q", "").strip()

    db = get_db()
    try:
        poems = db.execute("""
            SELECT id, title, author
            FROM poems
            WHERE title LIKE %s
            ORDER BY title ASC
            LIMIT 8
        """, (f"%{q}%",)).fetchall()
    finally:
        db.close()

    return jsonify([
        {
            "id": p["id"],
            "title": p["title"],
            "author": p["author"]
        }
        for p in poems
    ])


@app.route("/circles/<int:circle_id>/post", methods=["POST"])
@login_required
def circle_post(circle_id):
    db = get_db()
    try:
        role = _get_circle_role(db, circle_id, session["user_id"])
        if not role:
            abort(403)

        body = request.form.get("body", "").strip()
        poem_id = request.form.get("poem_id")

        if poem_id == "":
            poem_id = None

        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        db.execute(
            """
            INSERT INTO circle_posts 
            (circle_id, user_id, poem_id, body, is_pinned, posted_at)
            VALUES (%s, %s, %s, %s, 0, %s)
            """,
            (circle_id, session["user_id"], poem_id, body, now)
        )

        db.commit()

    finally:
        db.close()

    return redirect(url_for("circle_detail", circle_id=circle_id))

@app.route("/circles/<int:circle_id>/message", methods=["POST"])
@login_required
def circle_message(circle_id):
    db = get_db()
    try:
        role = _get_circle_role(db, circle_id, session["user_id"])
        if not role:
            abort(403)
        message = request.form.get("message", "").strip()
        if message:
            now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            db.execute(
                "INSERT INTO circle_messages (circle_id, user_id, message, sent_at) VALUES (%s,%s,%s,%s)",
                (circle_id, session["user_id"], message, now)
            )
            db.commit()
    finally:
        db.close()
    return redirect(url_for("circle_detail", circle_id=circle_id))


@app.route("/circles/<int:circle_id>/messages/api")
@login_required
def circle_messages_api(circle_id):
    db = get_db()
    try:
        role = _get_circle_role(db, circle_id, session["user_id"])
        if not role:
            abort(403)
        messages = db.execute("""
            SELECT cm.id, cm.message, cm.sent_at, u.username, cm.user_id
            FROM circle_messages cm JOIN users u ON cm.user_id=u.id
            WHERE cm.circle_id=%s ORDER BY cm.sent_at ASC
        """, (circle_id,)).fetchall()
    finally:
        db.close()
    return jsonify({"messages": [dict(m) for m in messages]})


@app.route("/circles/<int:circle_id>/pin/<int:post_id>", methods=["POST"])
@login_required
def circle_pin(circle_id, post_id):
    db = get_db()
    try:
        role = _get_circle_role(db, circle_id, session["user_id"])
        if role not in ("owner", "admin"):
            abort(403)
        current = db.execute("SELECT is_pinned FROM circle_posts WHERE id=%s AND circle_id=%s", (post_id, circle_id)).fetchone()
        if current:
            db.execute("UPDATE circle_posts SET is_pinned=%s WHERE id=%s", (0 if current["is_pinned"] else 1, post_id))
            db.commit()
    finally:
        db.close()
    return redirect(url_for("circle_detail", circle_id=circle_id))


@app.route("/circles/<int:circle_id>/leave", methods=["POST"])
@login_required
def circle_leave(circle_id):
    db = get_db()
    try:
        role = _get_circle_role(db, circle_id, session["user_id"])
        if role == "owner":
            return redirect(url_for("circle_detail", circle_id=circle_id))
        db.execute("DELETE FROM circle_members WHERE circle_id=%s AND user_id=%s",
                   (circle_id, session["user_id"]))
        db.commit()
    finally:
        db.close()
    return redirect(url_for("circles"))


# ============================================================
# LIVING POEM
# ============================================================
MAX_QUEUE = 20

def _rebuild_queue_positions(db):
    rows = db.execute("SELECT id FROM living_poem_queue ORDER BY position ASC, joined_at ASC").fetchall()
    for i, row in enumerate(rows, 1):
        db.execute("UPDATE living_poem_queue SET position=%s WHERE id=%s", (i, row["id"]))
    db.commit()


def _expire_overdue_queue(db):
    now = datetime.now()
    overdue = db.execute(
        "SELECT user_id FROM living_poem_queue WHERE position=1 AND deadline IS NOT NULL AND deadline<%s",
        (now.strftime("%Y-%m-%d %H:%M:%S"),)
    ).fetchall()
    for row in overdue:
        db.execute("DELETE FROM living_poem_queue WHERE user_id=%s", (row["user_id"],))
    if overdue:
        _rebuild_queue_positions(db)
        # Set deadline for new first user
        first = db.execute("SELECT * FROM living_poem_queue WHERE position=1").fetchone()
        if first:
            deadline = (now + timedelta(hours=24)).strftime("%Y-%m-%d %H:%M:%S")
            db.execute("UPDATE living_poem_queue SET deadline=%s WHERE user_id=%s",
                       (deadline, first["user_id"]))
    db.commit()


@app.route("/living-poem")
def living_poem():
    db = get_db()
    try:
        _expire_overdue_queue(db)
        lines = db.execute("""
            SELECT lpl.*, u.username
            FROM living_poem_lines lpl JOIN users u ON lpl.user_id=u.id
            ORDER BY lpl.line_number ASC
        """).fetchall()
        queue = db.execute("""
            SELECT lpq.*, u.username
            FROM living_poem_queue lpq JOIN users u ON lpq.user_id=u.id
            ORDER BY lpq.position ASC
        """).fetchall()
        total_lines = len(lines)
        my_pos = None
        if "user_id" in session:
            row = db.execute(
                "SELECT position FROM living_poem_queue WHERE user_id=%s", (session["user_id"],)
            ).fetchone()
            my_pos = row["position"] if row else None
        active_user = queue[0] if queue else None
        deadline_ts = None
        if active_user and active_user["deadline"]:
            deadline_ts = active_user["deadline"]
    finally:
        db.close()
    # Group lines into volumes of 100
    volumes = {}
    for line in lines:
        vol = ((line["line_number"] - 1) // 100) + 1
        volumes.setdefault(vol, []).append(line)
    return render_template("living_poem.html",
                           volumes=volumes, queue=queue,
                           total_lines=total_lines,
                           my_pos=my_pos,
                           active_user=active_user,
                           deadline_ts=deadline_ts)


@app.route("/living-poem/join", methods=["POST"])
@login_required
def living_poem_join():
    db = get_db()
    try:
        _expire_overdue_queue(db)
        # Already in queue%s
        if db.execute("SELECT 1 FROM living_poem_queue WHERE user_id=%s", (session["user_id"],)).fetchone():
            return redirect(url_for("living_poem"))
        count = db.execute("SELECT COUNT(*) FROM living_poem_queue").fetchone()[0]
        if count >= MAX_QUEUE:
            return redirect(url_for("living_poem"))
        # Check consecutive: last line author
        last_line = db.execute(
            "SELECT user_id FROM living_poem_lines ORDER BY line_number DESC LIMIT 1"
        ).fetchone()
        last_queue = db.execute(
            "SELECT user_id FROM living_poem_queue ORDER BY position DESC LIMIT 1"
        ).fetchone()
        if last_queue and last_queue["user_id"] == session["user_id"]:
            return redirect(url_for("living_poem"))
        now = datetime.now()
        position = count + 1
        deadline = None
        if position == 1:
            deadline = (now + timedelta(hours=24)).strftime("%Y-%m-%d %H:%M:%S")
        db.execute(
            "INSERT INTO living_poem_queue (user_id, position, joined_at, deadline) VALUES (%s,%s,%s,%s)",
            (session["user_id"], position, now.strftime("%Y-%m-%d %H:%M:%S"), deadline)
        )
        db.commit()
    finally:
        db.close()
    return redirect(url_for("living_poem"))


@app.route("/living-poem/leave", methods=["POST"])
@login_required
def living_poem_leave():
    db = get_db()
    try:
        db.execute("DELETE FROM living_poem_queue WHERE user_id=%s", (session["user_id"],))
        _rebuild_queue_positions(db)
        first = db.execute("SELECT * FROM living_poem_queue WHERE position=1").fetchone()
        if first and not first["deadline"]:
            deadline = (datetime.now() + timedelta(hours=24)).strftime("%Y-%m-%d %H:%M:%S")
            db.execute("UPDATE living_poem_queue SET deadline=%s WHERE user_id=%s",
                       (deadline, first["user_id"]))
        db.commit()
    finally:
        db.close()
    return redirect(url_for("living_poem"))


@app.route("/living-poem/add-line", methods=["POST"])
@login_required
def living_poem_add_line():
    db = get_db()
    try:
        _expire_overdue_queue(db)
        first = db.execute("SELECT * FROM living_poem_queue WHERE position=1").fetchone()
        if not first or first["user_id"] != session["user_id"]:
            return redirect(url_for("living_poem"))
        line_text = request.form.get("line_text", "").strip()
        if not line_text:
            return redirect(url_for("living_poem"))
        next_num = db.execute("SELECT COALESCE(MAX(line_number),0)+1 FROM living_poem_lines").fetchone()[0]
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        db.execute(
            "INSERT INTO living_poem_lines (line_number, user_id, line_text, added_at) VALUES (%s,%s,%s,%s)",
            (next_num, session["user_id"], line_text, now)
        )
        db.execute("DELETE FROM living_poem_queue WHERE user_id=%s", (session["user_id"],))
        _rebuild_queue_positions(db)
        first_next = db.execute("SELECT * FROM living_poem_queue WHERE position=1").fetchone()
        if first_next:
            deadline = (datetime.now() + timedelta(hours=24)).strftime("%Y-%m-%d %H:%M:%S")
            db.execute("UPDATE living_poem_queue SET deadline=%s WHERE user_id=%s",
                       (deadline, first_next["user_id"]))
            create_notification(first_next["user_id"], "It's your turn to add a line to the Living Poem! You have 24 hours.")
        db.commit()
    finally:
        db.close()
    return redirect(url_for("living_poem"))


# ============================================================
# FEATURES: NEW PLATFORM FEATURES (1-6)
# ============================================================

AUDIO_FOLDER = os.path.join("static", "audio_uploads")
ALLOWED_AUDIO = {"mp3", "wav", "ogg", "m4a", "webm"}

def allowed_audio_file(filename):
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_AUDIO

# ── FEATURE 1: RECITATIONS ──────────────────────────────────

@app.route("/poem/<int:poem_id>/recitations")
def poem_recitations(poem_id):
    db = get_db()
    try:
        poem = db.execute(
            "SELECT * FROM poems WHERE id=%s AND status='published'", (poem_id,)
        ).fetchone()
        if not poem:
            abort(404)
        recitations = db.execute("""
            SELECT r.*, u.username, u.profile_picture,
                   (SELECT COUNT(*) FROM recitation_likes  WHERE recitation_id=r.id) AS likes,
                   (SELECT COUNT(*) FROM recitation_comments WHERE recitation_id=r.id) AS cmt_count
            FROM poem_recitations r
            JOIN users u ON r.user_id = u.id
            WHERE r.poem_id = %s
            ORDER BY r.uploaded_at DESC
        """, (poem_id,)).fetchall()
        user_liked = set()
        if session.get("user_id"):
            rows = db.execute("""
                SELECT recitation_id FROM recitation_likes
                WHERE user_id=%s AND recitation_id IN (
                    SELECT id FROM poem_recitations WHERE poem_id=%s
                )
            """, (session["user_id"], poem_id)).fetchall()
            user_liked = {r["recitation_id"] for r in rows}
        return render_template("recitations.html",
                               poem=poem, recitations=recitations, user_liked=user_liked,
                               current_user_id=session.get("user_id"))
    finally:
        db.close()


@app.route("/poem/<int:poem_id>/recitations/upload", methods=["POST"])
@login_required
def upload_recitation(poem_id):
    db = get_db()
    try:
        poem = db.execute(
            "SELECT * FROM poems WHERE id=%s AND status='published'", (poem_id,)
        ).fetchone()
        if not poem:
            abort(404)
        f = request.files.get("audio")
        if not f or not f.filename or not allowed_audio_file(f.filename):
            flash("Please choose a valid audio file (mp3, wav, ogg, m4a).")
            return redirect(url_for("poem_recitations", poem_id=poem_id))
        os.makedirs(AUDIO_FOLDER, exist_ok=True)
        ext = f.filename.rsplit(".", 1)[1].lower()
        filename = f"{secrets.token_hex(10)}.{ext}"
        f.save(os.path.join(AUDIO_FOLDER, filename))
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        db.execute(
            "INSERT INTO poem_recitations (poem_id, user_id, filename, uploaded_at) VALUES (%s,%s,%s,%s)",
            (poem_id, session["user_id"], filename, now)
        )
        db.commit()
        flash("Your recitation was uploaded successfully.")
    finally:
        db.close()
    return redirect(url_for("poem_recitations", poem_id=poem_id))


@app.route("/recitation/<int:rid>/like", methods=["POST"])
@login_required
def toggle_recitation_like(rid):
    db = get_db()
    try:
        exists = db.execute(
            "SELECT id FROM recitation_likes WHERE recitation_id=%s AND user_id=%s",
            (rid, session["user_id"])
        ).fetchone()
        if exists:
            db.execute("DELETE FROM recitation_likes WHERE recitation_id=%s AND user_id=%s",
                       (rid, session["user_id"]))
            liked = False
        else:
            db.execute("INSERT INTO recitation_likes (recitation_id, user_id) VALUES (%s,%s)",
                       (rid, session["user_id"]))
            liked = True
        count = db.execute(
            "SELECT COUNT(*) FROM recitation_likes WHERE recitation_id=%s", (rid,)
        ).fetchone()[0]
        db.commit()
    finally:
        db.close()
    return jsonify({"liked": liked, "count": count})


@app.route("/recitation/<int:rid>/comments", methods=["GET", "POST"])
def recitation_comments_api(rid):
    db = get_db()
    try:
        if request.method == "POST":
            if not session.get("user_id"):
                return jsonify({"error": "Login required"}), 401
            text = (request.json or {}).get("comment", "").strip()
            if not text:
                return jsonify({"error": "Empty comment"}), 400
            now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            db.execute(
                "INSERT INTO recitation_comments (recitation_id, user_id, comment, created_at) VALUES (%s,%s,%s,%s)",
                (rid, session["user_id"], text, now)
            )
            db.commit()
            u = db.execute("SELECT username FROM users WHERE id=%s", (session["user_id"],)).fetchone()
            return jsonify({"success": True, "username": u["username"], "comment": text, "created_at": now})
        comments = db.execute("""
            SELECT rc.id, rc.comment, rc.created_at, u.username
            FROM recitation_comments rc JOIN users u ON rc.user_id=u.id
            WHERE rc.recitation_id=%s ORDER BY rc.created_at ASC
        """, (rid,)).fetchall()
        return jsonify([dict(c) for c in comments])
    finally:
        db.close()


# ── FEATURE 2: LINE-BY-LINE COMMENTS ────────────────────────

@app.route("/poem/<int:poem_id>/line-counts")
def line_comment_counts(poem_id):
    db = get_db()
    try:
        rows = db.execute("""
            SELECT line_index, COUNT(*) AS cnt
            FROM line_comments WHERE poem_id=%s
            GROUP BY line_index
        """, (poem_id,)).fetchall()
        return jsonify({r["line_index"]: r["cnt"] for r in rows})
    finally:
        db.close()


@app.route("/poem/<int:poem_id>/line/<int:line_index>/comments", methods=["GET", "POST"])
def line_comments_api(poem_id, line_index):
    db = get_db()
    try:
        if request.method == "POST":
            if not session.get("user_id"):
                return jsonify({"error": "Login required"}), 401
            data = request.json or {}
            text = data.get("comment", "").strip()
            if not text:
                return jsonify({"error": "Empty"}), 400
            now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            db.execute(
                "INSERT INTO line_comments (poem_id, user_id, line_index, comment, created_at) VALUES (%s,%s,%s,%s,%s)",
                (poem_id, session["user_id"], line_index, text, now)
            )
            db.commit()
            u = db.execute("SELECT username FROM users WHERE id=%s", (session["user_id"],)).fetchone()
            return jsonify({"success": True, "username": u["username"], "comment": text, "created_at": now})
        rows = db.execute("""
            SELECT lc.comment, lc.created_at, u.username
            FROM line_comments lc JOIN users u ON lc.user_id=u.id
            WHERE lc.poem_id=%s AND lc.line_index=%s
            ORDER BY lc.created_at ASC
        """, (poem_id, line_index)).fetchall()
        return jsonify([dict(r) for r in rows])
    finally:
        db.close()


# ── FEATURE 3: COLLECTIONS ──────────────────────────────────

@app.route("/collections")
@login_required
def my_collections():
    db = get_db()
    try:
        cols = db.execute("""
            SELECT c.*,
                   (SELECT COUNT(*) FROM collection_poems WHERE collection_id=c.id) AS poem_count
            FROM collections c WHERE c.user_id=%s
            ORDER BY c.created_at DESC
        """, (session["user_id"],)).fetchall()
        return render_template("my_collections.html", collections=cols)
    finally:
        db.close()


@app.route("/collections/explore")
def explore_collections():
    db = get_db()
    try:
        cols = db.execute("""
            SELECT c.*, u.username,
                   (SELECT COUNT(*) FROM collection_poems WHERE collection_id=c.id) AS poem_count,
                   (SELECT COUNT(*) FROM collection_follows WHERE collection_id=c.id) AS followers
            FROM collections c JOIN users u ON c.user_id=u.id
            WHERE c.is_public=1
            ORDER BY followers DESC, c.created_at DESC
            LIMIT 60
        """).fetchall()
        return render_template("explore_collections.html", collections=cols)
    finally:
        db.close()


@app.route("/collections/new", methods=["GET", "POST"])
@login_required
def new_collection():
    if request.method == "POST":
        name      = request.form.get("name", "").strip()
        desc      = request.form.get("description", "").strip()
        is_public = 1 if request.form.get("is_public") == "1" else 0
        if not name:
            flash("Collection name is required.")
            return redirect(url_for("new_collection"))
        db = get_db()
        try:
            now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            db.execute(
                "INSERT INTO collections (user_id, name, description, is_public, created_at) VALUES (%s,%s,%s,%s,%s)",
                (session["user_id"], name, desc, is_public, now)
            )
            db.commit()
        finally:
            db.close()
        flash("Collection created.")
        return redirect(url_for("my_collections"))
    return render_template("new_collection.html")


@app.route("/collections/<int:cid>")
def collection_detail(cid):
    db = get_db()
    try:
        col = db.execute("""
            SELECT c.*, u.username FROM collections c
            JOIN users u ON c.user_id=u.id WHERE c.id=%s
        """, (cid,)).fetchone()
        if not col:
            abort(404)
        if not col["is_public"] and col["user_id"] != session.get("user_id"):
            abort(403)
        poems = db.execute("""
            SELECT p.*, cp.added_at FROM collection_poems cp
            JOIN poems p ON cp.poem_id=p.id
            WHERE cp.collection_id=%s ORDER BY cp.added_at DESC
        """, (cid,)).fetchall()
        is_following = False
        follower_count = db.execute(
            "SELECT COUNT(*) FROM collection_follows WHERE collection_id=%s", (cid,)
        ).fetchone()[0]
        if session.get("user_id"):
            is_following = bool(db.execute(
                "SELECT 1 FROM collection_follows WHERE collection_id=%s AND user_id=%s",
                (cid, session["user_id"])
            ).fetchone())
        return render_template("collection_detail.html", col=col, poems=poems,
                               is_following=is_following, follower_count=follower_count,
                               current_user_id=session.get("user_id"))
    finally:
        db.close()


@app.route("/collections/<int:cid>/follow", methods=["POST"])
@login_required
def toggle_collection_follow(cid):
    db = get_db()
    try:
        exists = db.execute(
            "SELECT 1 FROM collection_follows WHERE collection_id=%s AND user_id=%s",
            (cid, session["user_id"])
        ).fetchone()
        if exists:
            db.execute("DELETE FROM collection_follows WHERE collection_id=%s AND user_id=%s",
                       (cid, session["user_id"]))
            following = False
        else:
            db.execute("INSERT INTO collection_follows (collection_id, user_id) VALUES (%s,%s)",
                       (cid, session["user_id"]))
            following = True
        count = db.execute(
            "SELECT COUNT(*) FROM collection_follows WHERE collection_id=%s", (cid,)
        ).fetchone()[0]
        db.commit()
    finally:
        db.close()
    return jsonify({"following": following, "count": count})


@app.route("/collections/<int:cid>/delete", methods=["POST"])
@login_required
def delete_collection(cid):
    db = get_db()
    try:
        col = db.execute("SELECT * FROM collections WHERE id=%s AND user_id=%s",
                         (cid, session["user_id"])).fetchone()
        if not col:
            abort(403)
        db.execute("DELETE FROM collection_poems WHERE collection_id=%s", (cid,))
        db.execute("DELETE FROM collection_follows WHERE collection_id=%s", (cid,))
        db.execute("DELETE FROM collections WHERE id=%s", (cid,))
        db.commit()
    finally:
        db.close()
    flash("Collection deleted.")
    return redirect(url_for("my_collections"))


@app.route("/collections/<int:cid>/add", methods=["POST"])
@login_required
def collection_add_poem(cid):
    db = get_db()
    try:
        col = db.execute("SELECT * FROM collections WHERE id=%s AND user_id=%s",
                         (cid, session["user_id"])).fetchone()
        if not col:
            return jsonify({"error": "Not yours"}), 403
        poem_id = request.json.get("poem_id") if request.is_json else request.form.get("poem_id")
        if not poem_id:
            return jsonify({"error": "No poem_id"}), 400
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        try:
            db.execute(
                "INSERT INTO collection_poems (collection_id, poem_id, added_at) VALUES (%s,%s,%s)",
                (cid, int(poem_id), now)
            )
            db.commit()
            return jsonify({"success": True})
        except Exception:
            return jsonify({"error": "Already in collection"}), 409
    finally:
        db.close()


@app.route("/collections/<int:cid>/remove", methods=["POST"])
@login_required
def collection_remove_poem(cid):
    db = get_db()
    try:
        col = db.execute("SELECT * FROM collections WHERE id=%s AND user_id=%s",
                         (cid, session["user_id"])).fetchone()
        if not col:
            return jsonify({"error": "Not yours"}), 403
        poem_id = request.json.get("poem_id") if request.is_json else request.form.get("poem_id")
        db.execute("DELETE FROM collection_poems WHERE collection_id=%s AND poem_id=%s",
                   (cid, int(poem_id)))
        db.commit()
        return jsonify({"success": True})
    finally:
        db.close()


@app.route("/poem/<int:poem_id>/add-to-collection", methods=["GET", "POST"])
@login_required
def add_to_collection(poem_id):
    db = get_db()
    try:
        poem = db.execute(
            "SELECT * FROM poems WHERE id=%s AND status='published'", (poem_id,)
        ).fetchone()
        if not poem:
            abort(404)
        if request.method == "POST":
            cid = request.form.get("collection_id", type=int)
            if cid:
                col = db.execute("SELECT * FROM collections WHERE id=%s AND user_id=%s",
                                 (cid, session["user_id"])).fetchone()
                if col:
                    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    try:
                        db.execute(
                            "INSERT INTO collection_poems (collection_id, poem_id, added_at) VALUES (%s,%s,%s)",
                            (cid, poem_id, now)
                        )
                        db.commit()
                        flash("Added to collection.")
                    except Exception:
                        flash("Already in that collection.")
            return redirect(url_for("poem_detail", poem_id=poem_id))
        my_cols = db.execute(
            "SELECT * FROM collections WHERE user_id=%s ORDER BY created_at DESC",
            (session["user_id"],)
        ).fetchall()
        already_in = set()
        for c in my_cols:
            if db.execute(
                "SELECT 1 FROM collection_poems WHERE collection_id=%s AND poem_id=%s",
                (c["id"], poem_id)
            ).fetchone():
                already_in.add(c["id"])
        return render_template("add_to_collection.html", poem=poem,
                               my_cols=my_cols, already_in=already_in)
    finally:
        db.close()


# ── FEATURE 4: DISCOVER ─────────────────────────────────────

@app.route("/discover")
def discover():
    db = get_db()
    try:
        uid = session.get("user_id")
        has_likes = _table_exists(db, "poem_likes")

        # trending: weighted score = views + 3*likes + 5*saves
        like_expr = "(SELECT COUNT(*) FROM poem_likes WHERE poem_id=p.id)" if has_likes else "0"
        trending = db.execute(f"""
            SELECT p.*, u.username,
                   {like_expr} AS like_count,
                   (SELECT COUNT(*) FROM saved_poems WHERE poem_id=p.id) AS save_count
            FROM poems p JOIN users u ON p.user_id=u.id
            WHERE p.status='published'
            ORDER BY (COALESCE(p.view_count,0) + 3*{like_expr} + 5*(SELECT COUNT(*) FROM saved_poems WHERE poem_id=p.id)) DESC
            LIMIT 10
        """).fetchall()

        # personalised
        recommended = []
        if uid and has_likes:
            liked_genres = db.execute("""
                SELECT DISTINCT p.genre FROM poem_likes pl
                JOIN poems p ON pl.poem_id=p.id WHERE pl.user_id=%s
            """, (uid,)).fetchall()
            genres = [g["genre"] for g in liked_genres if g["genre"]]
            if genres:
                ph = ",".join("%s" * len(genres))
                recommended = db.execute(f"""
                    SELECT p.*, u.username FROM poems p
                    JOIN users u ON p.user_id=u.id
                    WHERE p.status='published' AND p.genre IN ({ph})
                    AND p.id NOT IN (SELECT poem_id FROM poem_likes WHERE user_id=%s)
                    ORDER BY RANDOM() LIMIT 8
                """, (*genres, uid)).fetchall()

        # followed poets
        following_poems = []
        if uid:
            following_poems = db.execute("""
                SELECT p.*, u.username FROM poems p
                JOIN users u ON p.user_id=u.id
                JOIN followers f ON f.following_id=p.user_id
                WHERE f.follower_id=%s AND p.status='published'
                ORDER BY p.id DESC LIMIT 8
            """, (uid,)).fetchall()

        # new arrivals
        new_poems = db.execute("""
            SELECT p.*, u.username FROM poems p
            JOIN users u ON p.user_id=u.id
            WHERE p.status='published'
            ORDER BY p.id DESC LIMIT 8
        """).fetchall()

        return render_template("discover.html",
                               trending=trending, recommended=recommended,
                               following_poems=following_poems, new_poems=new_poems,
                               current_user_id=uid)
    finally:
        db.close()


# ── FEATURE 5: EXPORT CARD ──────────────────────────────────

@app.route("/poem/<int:poem_id>/export")
def export_card(poem_id):
    db = get_db()
    try:
        poem = db.execute(
            "SELECT * FROM poems WHERE id=%s AND status='published'", (poem_id,)
        ).fetchone()
        if not poem:
            abort(404)
        return render_template("export_card.html", poem=poem)
    finally:
        db.close()


# ── FEATURE 6: POEM STATISTICS ──────────────────────────────

@app.route("/poem/<int:poem_id>/stats")
def poem_stats(poem_id):
    db = get_db()
    try:
        poem = db.execute(
            "SELECT * FROM poems WHERE id=%s AND status='published'", (poem_id,)
        ).fetchone()
        if not poem:
            abort(404)
        if session.get("user_id") != poem["user_id"] and not session.get("is_admin"):
            abort(403)

        has_likes = _table_exists(db, "poem_likes")
        like_count  = db.execute("SELECT COUNT(*) FROM poem_likes WHERE poem_id=%s", (poem_id,)).fetchone()[0] if has_likes else 0
        save_count  = db.execute("SELECT COUNT(*) FROM saved_poems WHERE poem_id=%s", (poem_id,)).fetchone()[0]
        quote_count = db.execute("SELECT COUNT(*) FROM saved_quotes WHERE poem_id=%s", (poem_id,)).fetchone()[0]
        comment_count = db.execute(
            "SELECT COUNT(*) FROM comments WHERE poem_id=%s AND deleted=0", (poem_id,)
        ).fetchone()[0]
        line_cmt_count = db.execute(
            "SELECT COUNT(*) FROM line_comments WHERE poem_id=%s", (poem_id,)
        ).fetchone()[0]
        recitation_count = db.execute(
            "SELECT COUNT(*) FROM poem_recitations WHERE poem_id=%s", (poem_id,)
        ).fetchone()[0]

        avg_row = db.execute(
            "SELECT AVG(rating) FROM comments WHERE poem_id=%s AND parent_id IS NULL AND deleted=0",
            (poem_id,)
        ).fetchone()[0]
        avg_rating = round(avg_row, 1) if avg_row else None

        view_count = poem["view_count"] or 0
        total_time = poem["total_time_spent"] or 0
        word_count = len(poem["text"].split())
        # avg reading time in seconds: total_time / view_count (real measured data)
        avg_reading_time = round(total_time / view_count, 1) if view_count > 0 else 0

        stats = dict(
            views=view_count,
            likes=like_count,
            saves=save_count,
            quotes=quote_count,
            comments=comment_count,
            line_comments=line_cmt_count,
            recitations=recitation_count,
            avg_rating=avg_rating,
            total_time=total_time,
            avg_reading_time=avg_reading_time,
            word_count=word_count,
        )
        return render_template("poem_stats.html", poem=poem, stats=stats)
    finally:
        db.close()


def _table_exists(db, name):
    return bool(db.execute(
        "SELECT 1 FROM information_schema.tables WHERE table_schema='public' AND table_name=%s", (name,)
    ).fetchone())



# ============================================================
# DELETE MESSAGE
# ============================================================
@app.route("/messages/delete/<int:message_id>", methods=["POST"])
@login_required
def delete_message(message_id):
    db = get_db()
    try:
        msg = db.execute("SELECT * FROM messages WHERE id=%s", (message_id,)).fetchone()
        if not msg:
            return jsonify({"success": False, "error": "Message not found"}), 404
        if msg["sender_id"] != session["user_id"]:
            return jsonify({"success": False, "error": "Not authorized"}), 403
        db.execute("DELETE FROM messages WHERE id=%s", (message_id,))
        db.commit()
    finally:
        db.close()
    return jsonify({"success": True})

# ============================================================
# LOGOUT
# ============================================================
@app.route("/logout")
def logout():
    _lang = session.get("lang")
    session.clear()
    if _lang:
        session["lang"] = _lang
    return redirect(url_for("login"))


# ============================================================
# DB MIGRATION — ensure E2E encryption columns exist + new features
# ============================================================
def migrate_db():
    """Add E2E encryption columns to messages table if missing.
    Also create new feature tables. Safe to call on every startup.
    """
    db = get_db()
    try:
        # --- E2E encryption migration ---
        cols = {row["column_name"] for row in db.execute("""
            SELECT column_name FROM information_schema.columns
            WHERE table_schema='public' AND table_name='messages'
        """).fetchall()}
        needed = {
            "encrypted_message":      "TEXT NOT NULL DEFAULT ''",
            "encrypted_key_sender":   "TEXT NOT NULL DEFAULT ''",
            "encrypted_key_receiver": "TEXT NOT NULL DEFAULT ''",
            "iv":                     "TEXT NOT NULL DEFAULT ''",
        }
        for col, col_def in needed.items():
            if col not in cols:
                db.execute(f"ALTER TABLE messages ADD COLUMN {col} {col_def}")

        # --- Feature 1: Poem Audio / Recitations ---
        db.executescript("""
            CREATE TABLE IF NOT EXISTS poem_recitations (
                id          SERIAL PRIMARY KEY,
                poem_id     INTEGER NOT NULL,
                user_id     INTEGER NOT NULL,
                filename    TEXT NOT NULL,
                duration    INTEGER DEFAULT 0,
                uploaded_at TEXT NOT NULL,
                FOREIGN KEY (poem_id) REFERENCES poems(id),
                FOREIGN KEY (user_id) REFERENCES users(id)
            );
            CREATE TABLE IF NOT EXISTS recitation_likes (
                id             SERIAL PRIMARY KEY,
                recitation_id  INTEGER NOT NULL,
                user_id        INTEGER NOT NULL,
                UNIQUE(recitation_id, user_id),
                FOREIGN KEY (recitation_id) REFERENCES poem_recitations(id),
                FOREIGN KEY (user_id) REFERENCES users(id)
            );
            CREATE TABLE IF NOT EXISTS recitation_comments (
                id             SERIAL PRIMARY KEY,
                recitation_id  INTEGER NOT NULL,
                user_id        INTEGER NOT NULL,
                comment        TEXT NOT NULL,
                created_at     TEXT NOT NULL,
                FOREIGN KEY (recitation_id) REFERENCES poem_recitations(id),
                FOREIGN KEY (user_id) REFERENCES users(id)
            );
        """)

        # --- Feature 2: Line-by-Line Comments ---
        db.executescript("""
            CREATE TABLE IF NOT EXISTS line_comments (
                id         SERIAL PRIMARY KEY,
                poem_id    INTEGER NOT NULL,
                user_id    INTEGER NOT NULL,
                line_index INTEGER NOT NULL,
                comment    TEXT NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY (poem_id) REFERENCES poems(id),
                FOREIGN KEY (user_id) REFERENCES users(id)
            );
        """)

        # --- Feature 3: Poetry Collections ---
        db.executescript("""
            CREATE TABLE IF NOT EXISTS collections (
                id          SERIAL PRIMARY KEY,
                user_id     INTEGER NOT NULL,
                name        TEXT NOT NULL,
                description TEXT DEFAULT '',
                is_public   INTEGER DEFAULT 1,
                created_at  TEXT NOT NULL,
                FOREIGN KEY (user_id) REFERENCES users(id)
            );
            CREATE TABLE IF NOT EXISTS collection_poems (
                id            SERIAL PRIMARY KEY,
                collection_id INTEGER NOT NULL,
                poem_id       INTEGER NOT NULL,
                added_at      TEXT NOT NULL,
                UNIQUE(collection_id, poem_id),
                FOREIGN KEY (collection_id) REFERENCES collections(id),
                FOREIGN KEY (poem_id) REFERENCES poems(id)
            );
            CREATE TABLE IF NOT EXISTS collection_follows (
                id            SERIAL PRIMARY KEY,
                collection_id INTEGER NOT NULL,
                user_id       INTEGER NOT NULL,
                UNIQUE(collection_id, user_id),
                FOREIGN KEY (collection_id) REFERENCES collections(id),
                FOREIGN KEY (user_id) REFERENCES users(id)
            );
        """)

        # --- Feature 4: Discover / Explore ---
        db.executescript("""
            CREATE TABLE IF NOT EXISTS poem_views_log (
                id         SERIAL PRIMARY KEY,
                poem_id    INTEGER NOT NULL,
                user_id    INTEGER,
                viewed_at  TEXT NOT NULL,
                FOREIGN KEY (poem_id) REFERENCES poems(id)
            );
        """)

        # --- Feature 6: Poem Statistics (view tracking already exists, just need quotes_count) ---
        # poems already have view_count, total_time_spent; we use existing tables for the rest

        # --- Schema constraints: add UNIQUE if not already present (idempotent) ---
        constraint_migrations = [
            """
            DO $$ BEGIN
                IF NOT EXISTS (
                    SELECT 1 FROM pg_constraint
                    WHERE conname = 'daily_game_questions_game_date_position_key'
                ) THEN
                    ALTER TABLE daily_game_questions
                        ADD CONSTRAINT daily_game_questions_game_date_position_key
                        UNIQUE (game_date, position);
                END IF;
            END $$;
            """,
        ]
        for stmt in constraint_migrations:
            try:
                db._cur.execute(stmt)
                db._conn.commit()
            except Exception as cm_err:
                db._conn.rollback()
                print(f"[migrate_db] constraint migration skipped: {cm_err}")

        db.commit()
    except Exception as e:
        print(f"[migrate_db] warning: {e}")
    finally:
        db.close()


# ============================================================
# RUN
# ============================================================
if __name__ == "__main__":
    with app.app_context():
        init_db()
        seed_badges()
    migrate_db()
    app.run(host="0.0.0.0", port=5000, debug=True)
