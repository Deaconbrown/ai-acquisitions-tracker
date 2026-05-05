import json
import re
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
import base64
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from googleapiclient.discovery import build
from googleapiclient.discovery import build as gmail_build
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

# Gmail notification settings
GMAIL_TOKEN_PATH = os.environ.get("GMAIL_TOKEN_PATH", r"C:\Users\Arran\.claude\credentials\gmail_send_token.pickle")
ALERT_FROM       = "arranwilliams@gmail.com"
ALERT_TO         = "arranwilliams@gmail.com"

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

def send_gmail_alert(subject, body, summary_text="", date_found="", feed_url="", link_text=""):
    # Sends an email alert via Gmail API when a new acquisition is found
    try:
        with open(GMAIL_TOKEN_PATH, "rb") as f:
            creds = pickle.load(f)

        if creds.expired and creds.refresh_token:
            creds.refresh(Request())
            with open(GMAIL_TOKEN_PATH, "wb") as f:
                pickle.dump(creds, f)

        service = gmail_build("gmail", "v1", credentials=creds, cache_discovery=False)

        message = MIMEMultipart("alternative")
        message["to"]      = ALERT_TO
        message["from"]    = ALERT_FROM
        message["subject"] = subject

        # Plain text fallback
        plain_part = MIMEText(body, "plain")

        # HTML version — XDA newsletter inspired style
        html_body = f"""
<!DOCTYPE html>
<html>
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
</head>
<body style="margin:0;padding:0;background-color:#f4f4f4;font-family:Arial,sans-serif;">

  <!-- Wrapper -->
  <table width="100%" cellpadding="0" cellspacing="0" style="background-color:#f4f4f4;padding:20px 0;">
    <tr><td align="center">
      <table width="600" cellpadding="0" cellspacing="0" style="max-width:600px;width:100%;">

        <!-- Header -->
        <tr>
          <td style="background-color:#1a1a2e;padding:24px 32px;border-radius:8px 8px 0 0;">
            <p style="margin:0;font-size:11px;color:#8888aa;letter-spacing:2px;text-transform:uppercase;">AI Acquisitions Tracker</p>
            <h1 style="margin:8px 0 0 0;font-size:22px;color:#ffffff;font-weight:700;">New Acquisition Alert</h1>
          </td>
        </tr>

        <!-- Date bar -->
        <tr>
          <td style="background-color:#16213e;padding:10px 32px;">
            <p style="margin:0;font-size:12px;color:#aaaacc;letter-spacing:1px;">{datetime.now().strftime('%B %d, %Y — %H:%M')}</p>
          </td>
        </tr>

        <!-- Body -->
        <tr>
          <td style="background-color:#ffffff;padding:32px;">

            <!-- Headline -->
            <h2 style="margin:0 0 16px 0;font-size:20px;color:#1a1a2e;line-height:1.4;font-weight:700;">
              {subject.replace('AI Acquisition Alert: ', '')}
            </h2>

            <!-- Divider -->
            <hr style="border:none;border-top:2px solid #f0f0f0;margin:0 0 24px 0;">

            <!-- Summary -->
            <p style="margin:0 0 24px 0;font-size:15px;color:#444444;line-height:1.7;">
              {summary_text[:400]}
            </p>

            <!-- Details table -->
            <table width="100%" cellpadding="0" cellspacing="0" style="background-color:#f8f9ff;border-radius:6px;padding:0;margin-bottom:24px;">
              <tr>
                <td style="padding:14px 20px;border-bottom:1px solid #e8e8f0;">
                  <span style="font-size:11px;color:#8888aa;text-transform:uppercase;letter-spacing:1px;display:block;margin-bottom:4px;">Date Detected</span>
                  <span style="font-size:14px;color:#1a1a2e;font-weight:600;">{date_found}</span>
                </td>
              </tr>
              <tr>
                <td style="padding:14px 20px;">
                  <span style="font-size:11px;color:#8888aa;text-transform:uppercase;letter-spacing:1px;display:block;margin-bottom:4px;">Source Feed</span>
                  <span style="font-size:14px;color:#1a1a2e;font-weight:600;">{feed_url}</span>
                </td>
              </tr>
            </table>

            <!-- CTA Button -->
            <table cellpadding="0" cellspacing="0" style="margin-bottom:24px;">
              <tr>
                <td style="background-color:#1a1a2e;border-radius:6px;padding:0;">
                  <a href="{link_text}" style="display:inline-block;padding:14px 28px;font-size:14px;color:#ffffff;text-decoration:none;font-weight:700;letter-spacing:0.5px;">Read Full Article →</a>
                </td>
              </tr>
            </table>

            <!-- Drive link -->
            <p style="margin:0;font-size:13px;color:#888888;">
              View your full acquisitions dataset →
              <a href="https://drive.google.com/file/d/110_g-AuLvfKskFnyR96dWfB_OUW8Uodn" style="color:#4444cc;">Google Drive CSV</a>
            </p>

          </td>
        </tr>

        <!-- Footer -->
        <tr>
          <td style="background-color:#1a1a2e;padding:20px 32px;border-radius:0 0 8px 8px;">
            <p style="margin:0;font-size:11px;color:#666688;text-align:center;">
              AI Acquisitions Tracker · Automated alert · arranwilliams@gmail.com
            </p>
          </td>
        </tr>

      </table>
    </td></tr>
  </table>

</body>
</html>
"""
        html_part = MIMEText(html_body, "html")
        message.attach(plain_part)
        message.attach(html_part)

        raw = base64.urlsafe_b64encode(message.as_string().encode("utf-8")).decode()
        service.users().messages().send(userId="me", body={"raw": raw}).execute()
        log(f"Alert email sent: {subject}")

    except Exception as e:
        log(f"ERROR sending Gmail alert: {e}")

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

                # Google News wraps real URLs inside a redirect — extract the real one
                if "news.google.com" in feed_url:
                    # Extract real URL from href in the summary HTML
                    real_url_match = re.search(r'href="(https?://(?!news\.google)[^"]+)"', summary.decode_contents() if hasattr(summary, 'decode_contents') else "")
                    if real_url_match:
                        link_text = real_url_match.group(1)
                    # Extract clean text summary — strip all HTML tags
                    summary_text = re.sub(r'<[^>]+>', '', str(summary)).strip()
                    # Clean up any leftover whitespace
                    summary_text = re.sub(r'\s+', ' ', summary_text).strip()

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

                        # Send Gmail alert for every new match found
                        alert_subject = f"AI Acquisition Alert: {title_text[:60]}"
                        alert_body = (
                            f"New AI acquisition story detected.\n\n"
                            f"Headline: {title_text}\n"
                            f"Date found: {date_found}\n"
                            f"Summary: {summary_text[:300]}\n\n"
                            f"Source: {link_text}\n"
                            f"Feed: {feed_url}\n\n"
                            f"Open your acquisitions CSV on Google Drive to review:\n"
                            f"https://drive.google.com/file/d/110_g-AuLvfKskFnyR96dWfB_OUW8Uodn\n"
                        )
                        send_gmail_alert(
                            alert_subject,
                            alert_body,
                            summary_text=summary_text,
                            date_found=date_found,
                            feed_url=feed_url,
                            link_text=link_text
                        )

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
