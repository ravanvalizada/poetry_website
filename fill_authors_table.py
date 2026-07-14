import sqlite3


def get_db():
    db = sqlite3.connect("database.db")
    db.row_factory = sqlite3.Row
    return db

def fill_authors_table():
    db = get_db()
    cursor = db.cursor()

    users = cursor.execute("""
        SELECT * FROM users
        WHERE is_system_author = 1
    """).fetchall()

    for user in users:
        user_id = user["id"]
        full_name = user["username"]

        country = user["country"] if "country" in user.keys() else None
        period = user["period"] if "period" in user.keys() else None
        birth_year = user["birth_year"] if "birth_year" in user.keys() else None
        death_year = user["death_year"] if "death_year" in user.keys() else None
        bio = user["bio"] if "bio" in user.keys() else ""

        poem_count = cursor.execute("""
            SELECT COUNT(*) FROM poems
            WHERE user_id=? AND status='published'
        """, (user_id,)).fetchone()[0]

        avg_rating = cursor.execute("""
            SELECT AVG(c.rating)
            FROM comments c
            JOIN poems p ON c.poem_id = p.id
            WHERE p.user_id=? 
              AND c.deleted=0 
              AND p.status='published' 
              AND c.parent_id IS NULL
        """, (user_id,)).fetchone()[0]

        avg_rating = round(avg_rating, 2) if avg_rating else 0

        # 🔍 Check if author already exists
        exists = cursor.execute(
            "SELECT 1 FROM authors WHERE id=?",
            (user_id,)
        ).fetchone()

        if exists:
            # ✅ ONLY update dynamic values
            cursor.execute("""
                UPDATE authors
                SET poem_count=?, avg_rating=?
                WHERE id=?
            """, (poem_count, avg_rating, user_id))

        else:
            # ✅ Insert full row (first time only)
            cursor.execute("""
                INSERT INTO authors (
                    id, full_name, country, period,
                    birth_year, death_year, bio,
                    poem_count, avg_rating
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                user_id, full_name, country, period,
                birth_year, death_year, bio,
                poem_count, avg_rating
            ))

    db.commit()
    db.close()
    print("Authors table updated safely ✅")



#==========================
# ---------- RUN ----------
#==========================

if __name__ == "__main__":
    fill_authors_table()
