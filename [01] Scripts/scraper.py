import json
import sys
sys.stdout.reconfigure(encoding="utf-8")
import warnings
warnings.filterwarnings("ignore")
import csv
import os
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from bs4 import BeautifulSoup
from datetime import datetime
import pickle
import io
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseUpload
from google.auth.transport.requests import Request

# ── CONFIG ──────────────────────────────────────────────────────────────────
# Detect whether running in GitHub Actions or locally
RUNNING_IN_CLOUD = os.environ.get("GITHUB_ACTIONS") == "true"

if RUNNING_IN_CLOUD:
    # Cloud paths — passed in as environment variables by the workflow
    CONFIG_FILE = os.environ.get("CONFIG_FILE_PATH", "/tmp/sources.json")
    DATA_FILE   = "/tmp/acquisitions.csv"
    LOG_FILE    = "/tmp/scraper_log.txt"
else:
    # Local Windows paths
    BASE_DIR    = r"H:\[01] Google\Google Drive Arran\[00] AI\[01] Claude Space\[01] Projects\[01] AI Acquisitions Tracker"
    CONFIG_FILE = os.path.join(BASE_DIR, "[04] Config", "sources.json")
    DATA_FILE   = os.path.join(BASE_DIR, "[02] Data", "acquisitions.csv")
    LOG_FILE    = os.path.join(BASE_DIR, "[03] Logs", "scraper_log.txt")

# Google Drive settings
DRIVE_TOKEN_PATH = os.environ.get("DRIVE_TOKEN_PATH", r"C:\Users\Arran\.claude\credentials\personal_drive_token.pickle")
DRIVE_FOLDER_NAME = "AI Acquisitions Tracker"
DRIVE_FILE_NAME = "acquisitions.csv"

# ── LOAD CONFIG ──────────────────────────────────────────────────────────────
# Reads your sources.json file so keywords, companies and feeds are all
# controlled from one place — no need to edit this script directly
with open(CONFIG_FILE, "r") as f:
    config = json.load(f)

KEYWORDS     = [k.lower() for k in config["keywords"]]
AI_COMPANIES = [c.lower() for c in config["ai_companies"]]
RSS_FEEDS    = config["rss_feeds"]

# ── LOGGING ──────────────────────────────────────────────────────────────────
# Every time the scraper runs it writes a line to the log file
# so you can see what it found and when
def log(message):
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    full_message = f"[{timestamp}] {message}"
    print(full_message)
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(full_message + "\n")

def get_drive_service():
    # Loads the saved token and refreshes it if expired
    with open(DRIVE_TOKEN_PATH, "rb") as f:
        creds = pickle.load(f)
    if creds.expired and creds.refresh_token:
        creds.refresh(Request())
        with open(DRIVE_TOKEN_PATH, "wb") as f:
            pickle.dump(creds, f)
        log("Drive token refreshed.")
    return build("drive", "v3", credentials=creds)

def download_csv_from_drive(service):
    # Downloads the existing CSV from Google Drive before each run
    # so the duplicate check has the full history to work against
    try:
        results = service.files().list(
            q=f"name='{DRIVE_FILE_NAME}' and trashed=false",
            fields="files(id, name)"
        ).execute()
        files = results.get("files", [])

        if files:
            file_id = files[0]["id"]
            request = service.files().get_media(fileId=file_id)
            with open(DATA_FILE, "wb") as f:
                f.write(request.execute())
            log(f"Existing CSV downloaded from Drive ({file_id})")
        else:
            log("No existing CSV on Drive — starting fresh.")

    except Exception as e:
        log(f"ERROR downloading CSV from Drive: {e}")

def upload_to_drive(service):
    # Uploads the local CSV to Google Drive
    # If the file already exists on Drive it updates it — never duplicates
    try:
        # Check if file already exists on Drive
        results = service.files().list(
            q=f"name='{DRIVE_FILE_NAME}' and trashed=false",
            fields="files(id, name)"
        ).execute()
        files = results.get("files", [])

        # Read local CSV into memory
        with open(DATA_FILE, "rb") as f:
            file_content = f.read()

        media = MediaIoBaseUpload(
            io.BytesIO(file_content),
            mimetype="text/csv",
            resumable=False
        )

        if files:
            # File exists — update it
            file_id = files[0]["id"]
            service.files().update(
                fileId=file_id,
                media_body=media
            ).execute()
            log(f"Drive CSV updated (file ID: {file_id})")
        else:
            # File does not exist — create it
            file_metadata = {"name": DRIVE_FILE_NAME}
            created = service.files().create(
                body=file_metadata,
                media_body=media,
                fields="id"
            ).execute()
            log(f"Drive CSV created (file ID: {created.get('id')})")

    except Exception as e:
        log(f"ERROR uploading to Drive: {e}")

# ── CSV SETUP ────────────────────────────────────────────────────────────────
# Creates the CSV file with headers if it doesn't already exist
# If it does exist, new results are appended — nothing is overwritten
def initialise_csv():
    if not os.path.exists(DATA_FILE):
        with open(DATA_FILE, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(["Date Found", "Headline", "Summary", "Source URL", "Feed"])
        log("CSV created with headers.")

# ── DUPLICATE CHECK ──────────────────────────────────────────────────────────
# Checks if a URL has already been saved so we never save the same
# article twice even if it appears in multiple runs
def already_saved(url, title):
    if not os.path.exists(DATA_FILE):
        return False
    with open(DATA_FILE, "r", encoding="utf-8") as f:
        content = f.read()
        # Exact URL match
        if url in content:
            return True
        # Headline similarity check — strips common words and compares core terms
        title_words = set(title.lower().split())
        common_words = {"the","a","an","is","in","on","at","to","of","and","or","for","with","that","this","it","as","by","from","was","are","be","has","have","its","may","just","why","how","what","when","who","would","could","about","after","says","said","new"}
        title_core = title_words - common_words
        with open(DATA_FILE, "r", encoding="utf-8") as f2:
            reader = csv.reader(f2)
            next(reader, None)  # skip header
            for row in reader:
                if len(row) < 2:
                    continue
                saved_words = set(row[1].lower().split()) - common_words
                if len(title_core) > 0:
                    overlap = len(title_core & saved_words) / len(title_core)
                    if overlap >= 0.75:
                        return True
    return False

# ── MATCH CHECK ─────────────────────────────────────────────────────────────
# Returns True if the article headline or summary contains BOTH
# an AI company name AND an acquisition keyword
def is_relevant(text):
    text_lower = text.lower()
    has_keyword = any(k in text_lower for k in KEYWORDS)
    has_company = any(c in text_lower for c in AI_COMPANIES)
    return has_keyword and has_company

# ── RSS SCRAPER ──────────────────────────────────────────────────────────────
# Fetches each RSS feed, reads every article, checks if it is relevant,
# and saves matches to the CSV
def scrape_feeds():
    # Log rotation — keep last 1000 lines to prevent unbounded growth
    if os.path.exists(LOG_FILE):
        with open(LOG_FILE, "r", encoding="utf-8") as f:
            lines = f.readlines()
        if len(lines) > 1000:
            with open(LOG_FILE, "w", encoding="utf-8") as f:
                f.writelines(lines[-1000:])
            log("── Log rotated (kept last 1000 lines) ──")
    log("── Scraper run started ──")

    # If running in cloud — download existing CSV from Drive first
    # so duplicate checking works correctly across runs
    if RUNNING_IN_CLOUD:
        try:
            drive_service = get_drive_service()
            download_csv_from_drive(drive_service)
        except Exception as e:
            log(f"ERROR pre-loading CSV from Drive: {e}")

    # Retry logic — tries each feed up to 3 times with increasing wait between attempts
    session = requests.Session()
    retry_strategy = Retry(
        total=3,
        backoff_factor=2,
        status_forcelist=[429, 500, 502, 503, 504]
    )
    adapter = HTTPAdapter(max_retries=retry_strategy)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    initialise_csv()
    total_found = 0
    total_skipped = 0

    for feed_url in RSS_FEEDS:
        log(f"Checking feed: {feed_url}")
        try:
            response = session.get(feed_url, timeout=15)
            soup = BeautifulSoup(response.content, "xml")
            items = soup.find_all("item")

            if not items:
                items = soup.find_all("entry")  # Atom feed fallback

            for item in items:
                title   = item.find("title")
                summary = item.find("description") or item.find("summary")
                link    = item.find("link")

                title_text   = title.get_text(strip=True)   if title   else ""
                summary_text = summary.get_text(strip=True) if summary else ""
                link_text    = link.get_text(strip=True)    if link    else ""

                combined = f"{title_text} {summary_text}"

                if is_relevant(combined):
                    if already_saved(link_text, title_text):
                        log(f"SKIPPED (duplicate/similar): {title_text}")
                        total_skipped += 1
                    else:
                        date_found = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                        with open(DATA_FILE, "a", newline="", encoding="utf-8") as f:
                            writer = csv.writer(f)
                            writer.writerow([date_found, title_text, summary_text, link_text, feed_url])
                        log(f"MATCH SAVED: {title_text}")
                        total_found += 1

        except Exception as e:
            log(f"ERROR on {feed_url}: {e}")

    log(f"── Run complete. {total_found} new saved, {total_skipped} skipped. ──")
    # Upload latest CSV to Google Drive after every run
    try:
        drive_service = get_drive_service()
        upload_to_drive(drive_service)
        log("── Drive sync complete. ──\n")
    except Exception as e:
        log(f"ERROR connecting to Drive: {e}")
        log("── Drive sync FAILED. ──\n")

# ── RUN ──────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    scrape_feeds()
