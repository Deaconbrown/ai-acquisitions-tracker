import json
import csv
import os
import sys
sys.stdout.reconfigure(encoding="utf-8")
import yfinance as yf
import pandas as pd
from datetime import datetime, timedelta

# ── CONFIG ──────────────────────────────────────────────────────────────────
BASE_DIR        = r"H:\[01] Google\Google Drive Arran\[00] AI\[01] Claude Space\[01] Projects\[01] AI Acquisitions Tracker"
ACQUISITIONS    = os.path.join(BASE_DIR, "[02] Data", "acquisitions.csv")
TICKERS_FILE    = os.path.join(BASE_DIR, "[04] Config", "tickers.json")
STOCK_DATA_FILE = os.path.join(BASE_DIR, "[02] Data", "stock_data.csv")
LOG_FILE        = os.path.join(BASE_DIR, "[03] Logs", "stock_puller_log.txt")

# Days to look before and after the acquisition date
DAYS_BEFORE = 14
DAYS_AFTER  = 14

# ── LOGGING ──────────────────────────────────────────────────────────────────
def log(message):
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    full_message = f"[{timestamp}] {message}"
    print(full_message)
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(full_message + "\n")

# ── LOAD CONFIG ──────────────────────────────────────────────────────────────
with open(TICKERS_FILE, "r") as f:
    TICKERS = json.load(f)

# ── COMPANY DETECTION ────────────────────────────────────────────────────────
# Scans a headline for known company names and returns any that have
# a ticker symbol assigned in tickers.json
def find_tickers_in_headline(headline):
    found = []
    headline_lower = headline.lower()
    for company, ticker in TICKERS.items():
        if ticker and company.lower() in headline_lower:
            found.append((company, ticker))
    return found

# ── STOCK DATA PULLER ────────────────────────────────────────────────────────
# For a given ticker and date, pulls stock price history for the
# window defined by DAYS_BEFORE and DAYS_AFTER
def pull_stock_data(ticker, company, headline, acquisition_date_str):
    try:
        acquisition_date = datetime.strptime(acquisition_date_str[:10], "%Y-%m-%d")
        start_date = acquisition_date - timedelta(days=DAYS_BEFORE)
        end_date   = acquisition_date + timedelta(days=DAYS_AFTER)

        log(f"Pulling {ticker} ({company}) from {start_date.date()} to {end_date.date()}")

        stock = yf.Ticker(ticker)
        hist  = stock.history(start=start_date.strftime("%Y-%m-%d"),
                              end=end_date.strftime("%Y-%m-%d"))

        if hist.empty:
            log(f"WARNING: No data returned for {ticker} — may be delisted or invalid")
            return []

        rows = []
        for date, row in hist.iterrows():
            rows.append({
                "Acquisition Date": acquisition_date_str[:10],
                "Headline":         headline,
                "Company":          company,
                "Ticker":           ticker,
                "Price Date":       str(date.date()),
                "Open":             round(row["Open"], 4),
                "Close":            round(row["Close"], 4),
                "High":             round(row["High"], 4),
                "Low":              round(row["Low"], 4),
                "Volume":           int(row["Volume"]),
                "Days From Event":  (date.date() - acquisition_date.date()).days
            })

        log(f"Retrieved {len(rows)} trading days for {ticker}")
        return rows

    except Exception as e:
        log(f"ERROR pulling {ticker}: {e}")
        return []

# ── CSV WRITER ───────────────────────────────────────────────────────────────
def save_stock_data(all_rows):
    if not all_rows:
        log("No stock data to save.")
        return

    fieldnames = ["Acquisition Date", "Headline", "Company", "Ticker",
                  "Price Date", "Open", "Close", "High", "Low", "Volume",
                  "Days From Event"]

    with open(STOCK_DATA_FILE, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(all_rows)

    log(f"Stock data saved to {STOCK_DATA_FILE} ({len(all_rows)} rows)")

# ── MAIN ─────────────────────────────────────────────────────────────────────
def run():
    log("── Stock puller started ──")
    all_rows = []

    # Deduplicate by ticker + date before pulling from yfinance
    # Prevents pulling the same stock window multiple times when a company
    # appears in more than one headline on the same date
    already_pulled = set()

    with open(ACQUISITIONS, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            headline = row["Headline"]
            date     = row["Date Found"][:10]
            matches  = find_tickers_in_headline(headline)

            if not matches:
                log(f"No public company ticker found in: {headline[:60]}...")
                continue

            for company, ticker in matches:
                pull_key = (ticker, date)
                if pull_key in already_pulled:
                    log(f"SKIPPED duplicate pull: {ticker} for {date}")
                    continue
                already_pulled.add(pull_key)
                rows = pull_stock_data(ticker, company, headline, date)
                all_rows.extend(rows)

    save_stock_data(all_rows)
    log(f"── Stock puller complete. {len(all_rows)} total rows saved. ──\n")

if __name__ == "__main__":
    run()
