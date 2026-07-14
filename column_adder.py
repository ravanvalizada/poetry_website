import sqlite3

DB_PATH = "database.db"

conn = sqlite3.connect(DB_PATH)
c = conn.cursor()
idd = 44
name = "Molla Cümə"
by = 1800
dy = 1852
pd = "XIX century"
bio = """Mirza Shafi (1800-1852) was born in 1800 in Ganja. His grandfather Muhammed Shafi was a nobleman of Ganja, and his father Kerbelayi Sadykh was an architect in the palace of Javad-khan, the last ruler of Ganja.

The works of Mirza Shafi Vazeh mainly celebrated romantic love and the joy of life, though some poems criticized feudal society, slavery, and religious fanaticism. He also compiled the first anthology of Azerbaijani poetry and a Tatar–Russian dictionary for the Tiflis gymnasium with Russian teacher Ivan Grigoriev. For many years it was believed that the originals of Vazeh’s poems were lost and survived only in translation, but in 1963 originals in Azerbaijani and Persian were discovered. Only a few of his works remain, many translated by Naum Grebnev and Friedrich von Bodenstedt. Vazeh disliked printed books and preferred handwritten texts; he was also known for his beautiful calligraphy in the nastaliq style, as noted by Mirza Fatali Akhundov. Bodenstedt helped popularize his poetry in Europe through the book Songs of Mirza Shafi, which became extremely popular in Germany and was translated into many languages, attracting readers including Leo Tolstoy. Research on Vazeh’s works continues in Azerbaijan today.
"""
#c.execute("UPDATE authors SET birth_year = ?, death_year = ?, period = ?, bio = ? WHERE id = ?", (by, dy, pd, bio, idd))
c.execute("UPDATE authors SET country = ? WHERE id between ? and ?", ("Türkiye", 45, 135))
conn.commit()
conn.close()
