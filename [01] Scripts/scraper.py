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
# Folder IDs (not names) - Drive allows duplicate folder names, and this project's folder is
# nested several levels deep, so an ID is the only reliable way to target the right location.
DRIVE_DATA_FOLDER_ID    = "1Wvkd5qGii-l2iqkgQd8x127rYWGlciCN"  # [02] Data
DRIVE_REPORTS_FOLDER_ID = "1ZKdTiYn53lOG3famShtUQAmv-1kMh2hr"  # [05] Reports
DRIVE_FILE_NAME = "acquisitions.csv"

# Resend email notification settings
RESEND_API_KEY   = os.environ.get("RESEND_API_KEY", "")
ALERT_FROM       = os.environ.get("ALERT_FROM", "hello@senalai.com")
ALERT_TO         = os.environ.get("ALERT_TO", "arranwilliams@gmail.com")
# Email mode threshold — 0 stories: no email. 1: individual alert. 2–3: mini-digest. 4+: full digest.
ALERT_THRESHOLD  = 3

# ── LOAD CONFIG ──────────────────────────────────────────────────────────────
# Reads your sources.json file so keywords, companies and feeds are all
# controlled from one place — no need to edit this script directly
with open(CONFIG_FILE, "r") as f:
    config = json.load(f)

KEYWORDS         = [k.lower() for k in config["keywords"]]
AI_COMPANIES     = [c.lower() for c in config["ai_companies"]]
RSS_FEEDS        = config["rss_feeds"]
EXCLUDE_KEYWORDS = [e.lower() for e in config.get("exclude_keywords", [])]

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
            q=f"name='{DRIVE_FILE_NAME}' and '{DRIVE_DATA_FOLDER_ID}' in parents and trashed=false",
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
        # Check if file already exists on Drive, in the correct folder
        results = service.files().list(
            q=f"name='{DRIVE_FILE_NAME}' and '{DRIVE_DATA_FOLDER_ID}' in parents and trashed=false",
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
            # File does not exist — create it in the correct folder
            file_metadata = {"name": DRIVE_FILE_NAME, "parents": [DRIVE_DATA_FOLDER_ID]}
            created = service.files().create(
                body=file_metadata,
                media_body=media,
                fields="id"
            ).execute()
            log(f"Drive CSV created (file ID: {created.get('id')})")

    except Exception as e:
        log(f"ERROR uploading to Drive: {e}")

def upload_report_to_drive():
    # Uploads stock_data.csv and the pattern report to Google Drive after a cloud run.
    # Follows the same update-or-create pattern as upload_to_drive().
    try:
        service = get_drive_service()
    except Exception as e:
        log(f"ERROR connecting to Drive for report upload: {e}")
        return

    report_filename = f"pattern_report_{datetime.now().strftime('%Y-%m-%d')}.md"
    files_to_upload = [
        ("/tmp/stock_data.csv",       "stock_data.csv",  DRIVE_DATA_FOLDER_ID),
        (f"/tmp/{report_filename}",   report_filename,   DRIVE_REPORTS_FOLDER_ID),
    ]

    for local_path, drive_name, folder_id in files_to_upload:
        if not os.path.exists(local_path):
            log(f"  [DRIVE UPLOAD] SKIPPED — {drive_name} not found at {local_path}")
            continue
        try:
            results = service.files().list(
                q=f"name='{drive_name}' and '{folder_id}' in parents and trashed=false",
                fields="files(id, name)"
            ).execute()
            existing = results.get("files", [])

            with open(local_path, "rb") as f:
                file_content = f.read()

            mimetype = "text/csv" if drive_name.endswith(".csv") else "text/markdown"
            media = MediaIoBaseUpload(io.BytesIO(file_content), mimetype=mimetype, resumable=False)

            if existing:
                service.files().update(fileId=existing[0]["id"], media_body=media).execute()
                log(f"  [DRIVE UPLOAD] {drive_name} updated on Drive")
            else:
                service.files().create(body={"name": drive_name, "parents": [folder_id]}, media_body=media, fields="id").execute()
                log(f"  [DRIVE UPLOAD] {drive_name} created on Drive")

        except Exception as e:
            log(f"  [DRIVE UPLOAD ERROR] {drive_name}: {e}")

def send_gmail_alert(subject, body, summary_text="", date_found="", feed_url="", link_text="", html_override=None):
    # Sends an email alert via Resend API when a new acquisition is found
    try:
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
              AI Acquisitions Tracker · Automated alert · hello@senalai.com
            </p>
          </td>
        </tr>

      </table>
    </td></tr>
  </table>

</body>
</html>
"""
        payload = {
            "from": ALERT_FROM,
            "to": [ALERT_TO],
            "subject": subject,
            "html": html_override if html_override else html_body,
            "text": body,
        }
        response = requests.post(
            "https://api.resend.com/emails",
            headers={"Authorization": f"Bearer {RESEND_API_KEY}", "Content-Type": "application/json"},
            json=payload,
            timeout=15,
        )
        response.raise_for_status()
        log(f"Alert email sent: {subject}")

    except Exception as e:
        log(f"ERROR sending alert: {e}")

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
        common_words = {"the","a","an","is","in","on","at","to","of","and","or","for","with","that","this","it","as","by","from","was","are","be","has","have","its","may","just","why","how","what","when","who","would","could","about","after","says","said","new"}

        # Strip source suffix e.g. " - MarketWatch" or " - MacDailyNews"
        # before comparing so outlet names don't dilute the overlap score
        clean_title = re.sub(r'\s*-\s*\S+\s*$', '', title).strip()
        title_core = set(clean_title.lower().split()) - common_words

        with open(DATA_FILE, "r", encoding="utf-8") as f2:
            reader = csv.reader(f2)
            next(reader, None)  # skip header
            for row in reader:
                if len(row) < 2:
                    continue
                clean_saved = re.sub(r'\s*-\s*\S+\s*$', '', row[1]).strip()
                saved_words = set(clean_saved.lower().split()) - common_words
                if len(title_core) > 0:
                    overlap = len(title_core & saved_words) / len(title_core)
                    if overlap >= 0.70:
                        return True
    return False

# ── MATCH CHECK ─────────────────────────────────────────────────────────────
# Returns True if the article headline or summary contains BOTH
# an AI company name AND an acquisition keyword
def is_relevant(text):
    text_lower = text.lower()
    if EXCLUDE_KEYWORDS and any(e in text_lower for e in EXCLUDE_KEYWORDS):
        return False
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
    new_stories = []

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
                        new_stories.append({
                            "title": title_text,
                            "link": link_text,
                            "summary": summary_text,
                            "date": date_found,
                            "feed": feed_url
                        })

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

    # Email logic — exactly one email per run, or none if no stories found
    if total_found == 0:
        pass

    elif total_found == 1:
        story = new_stories[0]
        alert_subject = f"AI Acquisition Alert: {story['title'][:60]}"
        alert_body = (
            f"New AI acquisition story detected.\n\n"
            f"Headline: {story['title']}\n"
            f"Date found: {story['date']}\n"
            f"Summary: {story['summary'][:300]}\n\n"
            f"Source: {story['link']}\n"
            f"Feed: {story['feed']}\n\n"
            f"Open your acquisitions CSV on Google Drive to review:\n"
            f"https://drive.google.com/file/d/110_g-AuLvfKskFnyR96dWfB_OUW8Uodn\n"
        )
        send_gmail_alert(
            alert_subject,
            alert_body,
            summary_text=story['summary'],
            date_found=story['date'],
            feed_url=story['feed'],
            link_text=story['link']
        )
        log(f"Individual alert sent — {story['title'][:60]}")

    elif total_found <= ALERT_THRESHOLD:
        mini_subject = f"AI Acquisition Alert — {total_found} new stories"
        mini_body = f"{total_found} new AI acquisition stories found:\n\n"
        for i, story in enumerate(new_stories):
            mini_body += f"{i+1}. {story['title']}\n   {story['link']}\n\n"
        mini_body += f"Open your Google Drive CSV for full details:\nhttps://drive.google.com/file/d/110_g-AuLvfKskFnyR96dWfB_OUW8Uodn\n"
        html_mini = f"""<!DOCTYPE html>
<html>
<head><meta charset="UTF-8"></head>
<body style="margin:0;padding:0;background-color:#f4f4f4;font-family:Arial,sans-serif;">
  <table width="100%" cellpadding="0" cellspacing="0" style="background-color:#f4f4f4;padding:20px 0;">
    <tr><td align="center">
      <table width="600" cellpadding="0" cellspacing="0" style="max-width:600px;width:100%;">
        <tr>
          <td style="background-color:#1a1a2e;padding:24px 32px;border-radius:8px 8px 0 0;">
            <p style="margin:0;font-size:11px;color:#8888aa;letter-spacing:2px;text-transform:uppercase;">AI Acquisitions Tracker</p>
            <h1 style="margin:8px 0 0 0;font-size:22px;color:#ffffff;font-weight:700;">{total_found} new acquisition{'s' if total_found > 1 else ''}</h1>
          </td>
        </tr>
        <tr>
          <td style="background-color:#16213e;padding:10px 32px;">
            <p style="margin:0;font-size:12px;color:#aaaacc;letter-spacing:1px;">{datetime.now().strftime('%B %d, %Y — %H:%M')}</p>
          </td>
        </tr>
        <tr>
          <td style="background-color:#ffffff;padding:32px;">
            {''.join([f'<div style="border-left:3px solid #1a1a2e;padding:12px 16px;margin-bottom:16px;background:#f8f9ff;border-radius:0 6px 6px 0;"><p style="margin:0 0 6px 0;font-size:15px;color:#1a1a2e;font-weight:500;">{story["title"]}</p><a href="{story["link"]}" style="font-size:13px;color:#4444cc;">Read article →</a></div>' for story in new_stories])}
            <p style="margin:24px 0 0 0;font-size:13px;color:#888888;">
              View full dataset → <a href="https://drive.google.com/file/d/110_g-AuLvfKskFnyR96dWfB_OUW8Uodn" style="color:#4444cc;">Google Drive CSV</a>
            </p>
          </td>
        </tr>
        <tr>
          <td style="background-color:#1a1a2e;padding:20px 32px;border-radius:0 0 8px 8px;">
            <p style="margin:0;font-size:11px;color:#666688;text-align:center;">AI Acquisitions Tracker · Automated alert · hello@senalai.com</p>
          </td>
        </tr>
      </table>
    </td></tr>
  </table>
</body>
</html>"""
        send_gmail_alert(mini_subject, mini_body, html_override=html_mini)
        log(f"Mini-digest sent — {total_found} stories.")

    else:
        digest_subject = f"AI Acquisition Digest — {total_found} new stories found"
        digest_body = (
            f"{total_found} new AI acquisition stories were detected in this run.\n\n"
            f"Top stories:\n"
        )
        for i, story in enumerate(new_stories[:10]):
            digest_body += f"\n{i+1}. {story['title']}\n   {story['link']}\n"
        if total_found > 10:
            digest_body += f"\n...and {total_found - 10} more. Open your Google Drive CSV for the full list:\nhttps://drive.google.com/file/d/110_g-AuLvfKskFnyR96dWfB_OUW8Uodn\n"
        html_digest = f"""<!DOCTYPE html>
<html>
<head><meta charset="UTF-8"></head>
<body style="margin:0;padding:0;background-color:#f4f4f4;font-family:Arial,sans-serif;">
  <table width="100%" cellpadding="0" cellspacing="0" style="background-color:#f4f4f4;padding:20px 0;">
    <tr><td align="center">
      <table width="600" cellpadding="0" cellspacing="0" style="max-width:600px;width:100%;">
        <tr>
          <td style="background-color:#1a1a2e;padding:24px 32px;border-radius:8px 8px 0 0;">
            <p style="margin:0;font-size:11px;color:#8888aa;letter-spacing:2px;text-transform:uppercase;">AI Acquisitions Tracker</p>
            <h1 style="margin:8px 0 0 0;font-size:22px;color:#ffffff;font-weight:500;">Acquisition digest — {total_found} new stories</h1>
          </td>
        </tr>
        <tr>
          <td style="background-color:#16213e;padding:10px 32px;">
            <p style="margin:0;font-size:12px;color:#aaaacc;letter-spacing:1px;">{datetime.now().strftime('%B %d, %Y — %H:%M')}</p>
          </td>
        </tr>
        <tr>
          <td style="background-color:#ffffff;padding:32px;">
            <p style="margin:0 0 24px 0;font-size:15px;color:#444444;line-height:1.7;">
              {total_found} new AI acquisition stories were detected in this run. Here are the top stories:
            </p>
            {''.join([f'<div style="border-left:3px solid #1a1a2e;padding:12px 16px;margin-bottom:16px;background:#f8f9ff;border-radius:0 6px 6px 0;"><p style="margin:0 0 6px 0;font-size:15px;color:#1a1a2e;font-weight:500;">{story["title"]}</p><a href="{story["link"]}" style="font-size:13px;color:#4444cc;">Read article →</a></div>' for story in new_stories[:10]])}
            <p style="margin:24px 0 0 0;font-size:13px;color:#888888;">
              View full dataset → <a href="https://drive.google.com/file/d/110_g-AuLvfKskFnyR96dWfB_OUW8Uodn" style="color:#4444cc;">Google Drive CSV</a>
            </p>
          </td>
        </tr>
        <tr>
          <td style="background-color:#1a1a2e;padding:20px 32px;border-radius:0 0 8px 8px;">
            <p style="margin:0;font-size:11px;color:#666688;text-align:center;">AI Acquisitions Tracker · Digest alert · hello@senalai.com</p>
          </td>
        </tr>
      </table>
    </td></tr>
  </table>
</body>
</html>"""
        send_gmail_alert(digest_subject, digest_body, html_override=html_digest)
        log(f"Digest email sent — {total_found} stories.")

    trigger_downstream(total_found)

# ── AUTO-TRIGGER DOWNSTREAM ANALYSIS ────────────────────────────────────────
def trigger_downstream(total_found):
    if total_found == 0:
        return
    import subprocess
    scripts_dir = os.path.dirname(os.path.abspath(__file__))

    stock_ok = False
    analyser_ok = False

    log(f"── Auto-triggering stock puller ({total_found} new stories found) ──")
    try:
        result = subprocess.run(
            [sys.executable, os.path.join(scripts_dir, "stock_puller.py")],
            capture_output=True, text=True, encoding="utf-8"
        )
        if result.returncode == 0:
            stock_ok = True
            log("── Stock puller completed successfully ──")
        else:
            log(f"── Stock puller FAILED (exit {result.returncode}): {result.stderr[:300]}")
    except Exception as e:
        log(f"ERROR launching stock puller: {e}")

    log("── Auto-triggering pattern analyser ──")
    try:
        result = subprocess.run(
            [sys.executable, os.path.join(scripts_dir, "pattern_analyser.py")],
            capture_output=True, text=True, encoding="utf-8"
        )
        if result.returncode == 0:
            analyser_ok = True
            log("── Pattern analyser completed successfully ──")
        else:
            log(f"── Pattern analyser FAILED (exit {result.returncode}): {result.stderr[:300]}")
    except Exception as e:
        log(f"ERROR launching pattern analyser: {e}")

    if RUNNING_IN_CLOUD and stock_ok and analyser_ok:
        log("── Uploading report and stock data to Drive ──")
        upload_report_to_drive()
    else:
        log("── Local run — Drive upload skipped (files already on disk) ──")

# ── RUN ──────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    scrape_feeds()
