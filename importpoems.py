import requests
from bs4 import BeautifulSoup, NavigableString, Tag
import sqlite3
import time
import re
from datetime import datetime
from urllib.parse import urljoin

DB_PATH = "database.db"

AUTHOR_NAME = "Yusuf Hayaloğlu"
AUTHOR_ID = 135
AUTHOR_URL = "https://www.siir-defteri.com/turk-sairler/Yusuf-Hayaloglu/146"

BATCH_SIZE = 3
BATCH_INTERVAL = 20
MINI_DELAY = 3

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/145.0 Safari/537.36"
}

BASE_URL = "https://www.siir-defteri.com"


# =========================
# DB
# =========================
conn = sqlite3.connect(DB_PATH)
conn.row_factory = sqlite3.Row
c = conn.cursor()

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


# =========================
# HELPERS
# =========================
def clean_text(text: str) -> str:
    if not text:
        return ""
    text = text.replace("\xa0", " ")
    text = re.sub(r"\r", "", text)
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def poem_exists(title: str, author: str) -> bool:
    c.execute("SELECT id FROM poems WHERE title=? AND author=?", (title, author))
    return c.fetchone() is not None


def save_poem_to_db(title, text, genre="Şeir"):
    try:
        c.execute("""
            INSERT INTO poems (title, text, genre, language, author, user_id, date, status)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            title,
            text,
            genre,
            "Turkish",
            AUTHOR_NAME,
            AUTHOR_ID,
            datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "waiting"
        ))
        conn.commit()
        print(f"Saved: {title}")
    except sqlite3.IntegrityError:
        print(f"Already exists: {title}")
    except Exception as e:
        print(f"DB save error for '{title}': {e}")


def get_soup(url: str):
    r = requests.get(url, headers=HEADERS, timeout=20)
    r.raise_for_status()
    return BeautifulSoup(r.text, "html.parser")


# =========================
# STEP 1: GET ALL POEM LINKS
# =========================
def fetch_poem_links_from_poet_page(start_url):
    print(f"Opening poet page: {start_url}")
    soup = get_soup(start_url)

    poem_links = []

    # Right-side poem navigation block
    nav = soup.select_one("#nav-content")
    if not nav:
        print("Could not find #nav-content")
        return []

    for a in nav.select("ul.default-bullets li a[href]"):
        href = a.get("href", "").strip()
        title = clean_text(a.get_text(" ", strip=True))

        if not href or not title:
            continue

        full_url = urljoin(BASE_URL, href)
        poem_links.append((title, full_url))

    # dedupe
    seen = set()
    unique_links = []
    for title, url in poem_links:
        key = (title.lower(), url)
        if key not in seen:
            seen.add(key)
            unique_links.append((title, url))

    print(f"Found {len(unique_links)} poem links.")
    return unique_links


# =========================
# STEP 2: EXTRACT TITLE + TEXT
# =========================
def fetch_poem_content(url, fallback_title="Unknown"):
    print(f"Fetching poem page: {url}")

    try:
        soup = get_soup(url)
    except Exception as e:
        print(f"Request failed: {e}")
        return fallback_title, None

    article = soup.find("article")
    if not article:
        print("No <article> found.")
        return fallback_title, None

    # poem title in h4
    h4 = article.find("h4")
    title = clean_text(h4.get_text(" ", strip=True)) if h4 else fallback_title

    # Remove right navigation if inside article
    nav = article.select_one("#nav-content")
    if nav:
        nav.decompose()

    # Remove buttons / share sections / noisy blocks if present
    for bad in article.select("script, style, .hidden, .adsbygoogle, .social-share, .share, .buttons"):
        bad.decompose()

    # Collect poem text from article after h4
    lines = []
    started = False

    for child in article.children:
        if isinstance(child, Tag) and child.name == "h4":
            started = True
            continue

        if not started:
            continue

        # stop if another obvious block begins
        if isinstance(child, Tag):
            child_id = child.get("id", "")
            child_cls = " ".join(child.get("class", []))

            if child_id == "nav-content":
                break

            # Likely non-poem sections
            if "yorum" in child_cls.lower() or "comment" in child_cls.lower():
                break

            if child.name in ["script", "style"]:
                continue

            txt = child.get_text("\n", strip=True)
            txt = clean_text(txt)
            if txt:
                lines.append(txt)

        elif isinstance(child, NavigableString):
            txt = clean_text(str(child))
            if txt:
                lines.append(txt)

    poem_text = "\n".join(lines)
    poem_text = clean_text(poem_text)

    # optional cleanup: remove repeated title at beginning
    if poem_text.lower().startswith(title.lower()):
        poem_text = clean_text(poem_text[len(title):])

    if not poem_text:
        print(f"No poem text extracted for '{title}'")
        return title, None

    return title, poem_text


# =========================
# MAIN
# =========================
def main():
    poem_links = fetch_poem_links_from_poet_page(AUTHOR_URL)

    if not poem_links:
        print("No poem links found.")
        return

    total = len(poem_links)
    batch_count = (total + BATCH_SIZE - 1) // BATCH_SIZE

    print(f"\nTotal poems to process: {total}\n")

    for b in range(batch_count):
        start = b * BATCH_SIZE
        end = min(start + BATCH_SIZE, total)
        batch = poem_links[start:end]

        print(f"Processing batch {b+1}/{batch_count} ({start+1}-{end})")

        for link_title, url in batch:
            safe_title = link_title or "Unknown"

            if poem_exists(safe_title, AUTHOR_NAME):
                print(f"Already in DB, skipping: {safe_title}")
                continue

            title, text = fetch_poem_content(url, safe_title)

            if text:
                save_poem_to_db(title, text, genre="Şeir")

            time.sleep(MINI_DELAY)

        if b < batch_count - 1:
            print(f"Batch {b+1} done. Waiting {BATCH_INTERVAL} seconds...\n")
            time.sleep(BATCH_INTERVAL)

    print("Finished scraping.")


if __name__ == "__main__":
    main()