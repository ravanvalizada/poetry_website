import requests
from bs4 import BeautifulSoup
import sqlite3
import time
import re
from datetime import datetime

DB_PATH = "database.db"
AUTHOR_NAME = "Mirzə Şəfi Vazeh"
AUTHOR_ID = 44
AUTHOR_URL = "https://az.wikisource.org/wiki/Müəllif:Mirzə_Şəfi_Vazeh"

BATCH_SIZE = 3      # poems per mini-batch
BATCH_INTERVAL = 30 # seconds between larger batches
MINI_DELAY = 5      # seconds between mini-batch fetches

HEADERS = {
    "User-Agent": "PoemFetcherBot/1.0 (https://yourwebsite.com; email@example.com)"
}

# Connect to DB
conn = sqlite3.connect(DB_PATH)
c = conn.cursor()

# Create table if not exists
c.execute("""
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
conn.commit()


def normalize_genre(raw: str) -> str:
    """
    Converts section headings like:
    - 'Gəraylıları' -> 'Gəraylı'
    - 'Qoşmaları'   -> 'Qoşma'
    - 'Növhələri'   -> 'Növhə'
    """
    if not raw:
        return "poetry"

    g = raw.strip()

    # Remove common plural/collection suffixes for these headings
    # Azerbaijani: -ları / -ləri
    for suf in ("ları", "ləri", "lar", "lər"):
        if g.endswith(suf) and len(g) > len(suf):
            g = g[:-len(suf)].strip()
            break

    # Optional cleanup: collapse whitespace
    g = re.sub(r"\s+", " ", g)

    return g if g else "poetry"


def clean_heading_text(s: str) -> str:
    if not s:
        return ""
    # remove common "redaktə" / edit noise that sometimes gets into heading text
    s = re.sub(r"\bredaktə\b", "", s, flags=re.IGNORECASE)
    s = re.sub(r"\s+", " ", s).strip()
    return s

def fetch_author_poems():
    print(f"Fetching {AUTHOR_NAME} page: {AUTHOR_URL}")
    r = requests.get(AUTHOR_URL, headers=HEADERS)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")

    content_div = soup.select_one("div.mw-content-ltr.mw-parser-output[lang='az'][dir='ltr']")
    if not content_div:
        print("Could not find content div.")
        return []

    poems = []
    current_genre = "poetry"

    # Walk in DOM order (recursive) but only the tags we care about
    for el in content_div.find_all(["h2", "h3", "h4", "h5", "h6", "ul", "ol"], recursive=True):
        # Skip TOC/navboxes etc.
        if el.find_parent(id="toc") or (el.get("class") and ("toc" in el.get("class") or "navbox" in el.get("class"))):
            continue

        if el.name in ("h2", "h3", "h4", "h5", "h6"):
            heading_text = clean_heading_text(el.get_text(" ", strip=True))
            if heading_text:
                current_genre = normalize_genre(heading_text)
            continue

        # list of poems
        if el.name in ("ul", "ol"):
            for a in el.select(":scope > li > a[href]"):
                href = a.get("href", "")
                if not href.startswith("/wiki/"):
                    continue
                title = a.get_text(strip=True)
                url = "https://az.wikisource.org" + href
                poems.append((title, url, current_genre))

    # dedupe
    seen = set()
    uniq = []
    for t, u, g in poems:
        key = (t, u)
        if key not in seen:
            seen.add(key)
            uniq.append((t, u, g))

    print(f"Found {len(uniq)} poems on {AUTHOR_NAME} page (with genres).")
    return uniq

def save_poem_to_db(title, text, genre):
    try:
        c.execute(
            "INSERT INTO poems (title, text, genre, language, author, user_id, date, status) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (title, text, genre, "Azerbaijani", AUTHOR_NAME, AUTHOR_ID, datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "waiting")
        )
        conn.commit()
        print(f"Saved '{title}' [{genre}] by {AUTHOR_NAME} to DB")
    except sqlite3.IntegrityError:
        print(f"Poem '{title}' already exists, skipping.")
    except sqlite3.OperationalError as e:
        print(f"Error saving '{title}': {e}")


def fetch_poem_content(url, fallback_title):
    try:
        r = requests.get(url, headers=HEADERS)
        r.raise_for_status()
    except requests.RequestException as e:
        print(f"Failed to fetch {url}: {e}")
        return None, None

    soup = BeautifulSoup(r.text, "html.parser")

    title_tag = soup.find("h1", id="firstHeading")
    full_title = title_tag.get_text(strip=True) if title_tag else fallback_title
    title = re.split(r"\s*\(", full_title)[0].strip()

    poem_div = soup.find("div", class_="poem")
    if not poem_div:
        print(f"Could not find poem text for '{title}'")
        return title, None

    # Collect ALL <p> inside poem_div, but ignore empty ones (like <p><br></p>)
    paragraphs = poem_div.find_all("p")
    if not paragraphs:
        # Some pages may have poem text directly under div.poem
        paragraphs = [poem_div]

    stanzas = []
    current_lines = []
    br_count = 0
    got_any_text = False

    def flush_stanza():
        nonlocal current_lines
        if current_lines:
            stanzas.append("\n".join(current_lines).strip())
            current_lines = []

    for p in paragraphs:
        # If paragraph has no real text, skip
        if not p.get_text(strip=True):
            continue

        for node in p.children:
            if getattr(node, "name", None) == "br":
                br_count += 1
                continue

            txt = ""
            if isinstance(node, str):
                txt = node.strip()
            else:
                # For tags inside (i, b, span, a, etc.)
                txt = node.get_text(" ", strip=True)

            if txt:
                got_any_text = True
                # 2+ br means stanza break on Wikisource poems
                if br_count >= 2:
                    flush_stanza()
                br_count = 0
                current_lines.append(txt)

        # Paragraph boundary = stanza boundary (usually)
        flush_stanza()
        br_count = 0

    flush_stanza()

    if not got_any_text:
        print(f"Poem div exists but no text extracted for '{title}' (empty <p> issue).")
        return title, None

    poem_text = "\n\n".join([s for s in stanzas if s])
    poem_text = re.sub(r"[ \t]+\n", "\n", poem_text)  # trim line trailing spaces
    poem_text = re.sub(r"\n{3,}", "\n\n", poem_text).strip()  # collapse huge gaps
    return title, poem_text


def main():
    poems = fetch_author_poems()
    total = len(poems)
    print(f"Found {total} poems for {AUTHOR_NAME}.\n")

    batch_count = (total + BATCH_SIZE - 1) // BATCH_SIZE
    for b in range(batch_count):
        start = b * BATCH_SIZE
        end = min(start + BATCH_SIZE, total)
        batch = poems[start:end]

        print(f"Processing batch {b+1}/{batch_count} ({start+1}-{end}) for {AUTHOR_NAME}")
        for title, url, genre in batch:
            safe_title = title or "Unknown"

            # duplicate check (same title+author)
            c.execute("SELECT id FROM poems WHERE title=? AND author=?", (safe_title, AUTHOR_NAME))
            if c.fetchone():
                print(f"'{safe_title}' already in DB, skipping.")
                continue

            fetched_title, text = fetch_poem_content(url, safe_title)
            if text:
                save_poem_to_db(fetched_title, text, genre)

            time.sleep(MINI_DELAY)

        print(f"Batch {b+1} done. Waiting {BATCH_INTERVAL} seconds before next batch...\n")
        time.sleep(BATCH_INTERVAL)

    print("Finished fetching all poems.")


if __name__ == "__main__":
    main()