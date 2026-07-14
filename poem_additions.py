# upgrade.py
import sqlite3
from datetime import datetime

DB_PATH = "database.db"  # your SQLite DB file path
SYSTEM_AUTHOR_ID = 44    # system author user_id

def add_system_poem(nm, txt, gnr):
    db = sqlite3.connect(DB_PATH)
    db.row_factory = sqlite3.Row
    cursor = db.cursor()

    # Check if the poem already exists (optional)
    title = nm
    existing = cursor.execute("""
        SELECT 1 FROM poems 
        WHERE title=? AND user_id=?
    """, (title, SYSTEM_AUTHOR_ID)).fetchone()

    genre = gnr
    poem_text = txt

    if existing:
        print("Poem already exists for system author.")
        db.close()
        return

    # Insert new poem
    cursor.execute("""
        INSERT INTO poems (title, text, genre, language, author, date, user_id, status)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        title,
        poem_text,
        genre,
        "Azerbaijani",
        "Mirzə Şəfi Vazeh",
        datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        SYSTEM_AUTHOR_ID,
        "waiting"
    ))

    db.commit()
    db.close()
    print("System author poem added successfully!")

if __name__ == "__main__":
    add_system_poem("Tək bəndlər", """Ey bəşərin xilasına edilən kömək,
Ey göylərin ilk töhfəsi sevimli mələk!
Sənə xidmət edənlərə təsəllisən sən,
Müdriklərin ilk arzusu, ilk istəyisən.

Ancaq nadan çətin işdən qaçar asana,
Məhv olardım dirənmədən min yol əsana!
Ey göylərin ilk vergisi!... İnan ki, sənə
Daim sadiq qalasıyam gedincə sinə.

Qoy sən həqiqəti söyləyən zaman,
Qopsun min təhlükə, qopsun min tufan.
Gəl baxma bunlara, ey Mirzə Şəfi,
Uca tut daima arı, şərəfi!

Qəlbimin, ruhumun qanadıyla mən,
Çıxdım aydınlığa zülmət gecədən.
Gördüm şeriyyətlə həqiqətimi,
Tapdım həqiqətlə şeriyyətimi.

Min ev yıxıb, tikirsiniz bir boş minarə,
Ərşə çıxıb, qonaq gedin pərvərdigarə.
Hicran qəminin badeyi-gülgündür əlacı
Vazeh, danışırlar bunu meyxanələr içrə.""", "Şeir")