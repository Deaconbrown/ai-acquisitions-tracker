import csv
import os
import sys
import json
from datetime import datetime
from collections import defaultdict

sys.stdout.reconfigure(encoding="utf-8")

# ── CONFIG ──────────────────────────────────────────────────────────────────
RUNNING_IN_CLOUD = os.environ.get("GITHUB_ACTIONS") == "true"

if RUNNING_IN_CLOUD:
    STOCK_DATA_FILE = "/tmp/stock_data.csv"
    REPORT_FILE     = f"/tmp/pattern_report_{datetime.now().strftime('%Y-%m-%d')}.md"
    LOG_FILE        = "/tmp/pattern_analyser_log.txt"
else:
    BASE_DIR        = r"H:\[01] Google\Google Drive Arran\[00] AI\[01] Claude Space\[01] Projects\[01] AI Acquisitions Tracker"
    STOCK_DATA_FILE = os.path.join(BASE_DIR, "[02] Data", "stock_data.csv")
    REPORT_FILE     = os.path.join(BASE_DIR, "[05] Reports", f"pattern_report_{datetime.now().strftime('%Y-%m-%d')}.md")
    LOG_FILE        = os.path.join(BASE_DIR, "[03] Logs", "pattern_analyser_log.txt")

# Acquirer keywords — company must appear near these words to count as acquirer
# This reduces false positives like "fighting Apple" matching as Apple acquiring
ACQUIRER_SIGNALS = [
    "acquires", "acquisition", "acquired", "purchases", "purchased",
    "buys", "bought", "merger", "takes over", "took over",
    "deal worth", "billion deal", "million deal", "to acquire"
]

# ── LOGGING ──────────────────────────────────────────────────────────────────
def log(message):
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    full_message = f"[{timestamp}] {message}"
    print(full_message)
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(full_message + "\n")

# ── ROLE CHECK ───────────────────────────────────────────────────────────────
# Returns True only if the company name appears near an acquirer signal word
# in the headline — reduces false positives from mere mentions
def is_likely_acquirer(company, headline):
    headline_lower = headline.lower()
    company_lower  = company.lower()

    if company_lower not in headline_lower:
        return False

    # Find position of company name in headline
    company_pos = headline_lower.find(company_lower)

    # Check if any acquirer signal word appears within 60 characters of the company name
    for signal in ACQUIRER_SIGNALS:
        signal_pos = headline_lower.find(signal)
        if signal_pos != -1 and abs(signal_pos - company_pos) < 60:
            return True

    return False

# ── DEDUPLICATION ────────────────────────────────────────────────────────────
# Groups stock rows by ticker + acquisition date window
# Keeps only one set of price data per unique ticker/event combination
def deduplicate_windows(rows):
    seen = set()
    deduplicated = []
    for row in rows:
        key = (row["Ticker"], row["Acquisition Date"], row["Price Date"])
        if key not in seen:
            seen.add(key)
            deduplicated.append(row)
    return deduplicated

# ── PRICE MOVEMENT CALCULATOR ────────────────────────────────────────────────
# For a set of rows for one ticker/event, calculates:
# - Price 7 days before announcement
# - Price on announcement day (or nearest trading day)
# - Price 7 days after announcement
# - Percentage change before, on, and after
def calculate_movements(rows):
    # Sort by Days From Event
    rows_sorted = sorted(rows, key=lambda r: int(r["Days From Event"]))

    # Find closing prices at key points
    before_prices = [r for r in rows_sorted if -8 <= int(r["Days From Event"]) <= -1]
    on_day        = [r for r in rows_sorted if int(r["Days From Event"]) == 0]
    after_prices  = [r for r in rows_sorted if 1 <= int(r["Days From Event"]) <= 7]

    if not before_prices or not after_prices:
        return None

    price_before = float(before_prices[0]["Close"])   # earliest close in window
    price_on     = float(on_day[0]["Close"]) if on_day else float(before_prices[-1]["Close"])
    price_after  = float(after_prices[-1]["Close"])   # latest close in window

    pct_change_to_event  = round(((price_on - price_before) / price_before) * 100, 2)
    pct_change_after     = round(((price_after - price_on) / price_on) * 100, 2)
    pct_change_total     = round(((price_after - price_before) / price_before) * 100, 2)

    return {
        "price_before":         price_before,
        "price_on":             price_on,
        "price_after":          price_after,
        "pct_change_to_event":  pct_change_to_event,
        "pct_change_after":     pct_change_after,
        "pct_change_total":     pct_change_total
    }

# ── REPORT WRITER ─────────────────────────────────────────────────────────────
def write_report(results):
    lines = []
    lines.append(f"# AI Acquisitions — Pattern Analysis Report")
    lines.append(f"**Generated:** {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    lines.append(f"**Events analysed:** {len(results)}")
    lines.append("")
    lines.append("---")
    lines.append("")

    if not results:
        lines.append("No analysable events found. More acquisition data needed.")
        lines.append("")
        lines.append("**Why:** Pattern analysis requires a publicly traded company")
        lines.append("to be clearly identified as the acquirer in the headline.")
        lines.append("Current dataset has no confirmed acquirer/ticker matches.")
        lines.append("")
        lines.append("**Next steps:** Wait for more acquisition stories to be scraped,")
        lines.append("or manually add known acquisitions to acquisitions.csv.")
    else:
        for r in results:
            lines.append(f"## {r['company']} ({r['ticker']}) — {r['headline'][:80]}...")
            lines.append(f"**Acquisition date:** {r['acquisition_date']}")
            lines.append(f"**Price 7 days before:** ${r['movements']['price_before']}")
            lines.append(f"**Price on announcement:** ${r['movements']['price_on']}")
            lines.append(f"**Price 7 days after:** ${r['movements']['price_after']}")
            lines.append(f"**Change to announcement:** {r['movements']['pct_change_to_event']}%")
            lines.append(f"**Change after announcement:** {r['movements']['pct_change_after']}%")
            lines.append(f"**Total change across window:** {r['movements']['pct_change_total']}%")
            lines.append("")
            lines.append("---")
            lines.append("")

    with open(REPORT_FILE, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

    log(f"Report written to {REPORT_FILE}")

# ── MAIN ─────────────────────────────────────────────────────────────────────
def run():
    log("── Pattern analyser started ──")

    # Load stock data
    with open(STOCK_DATA_FILE, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        all_rows = list(reader)

    log(f"Loaded {len(all_rows)} rows from stock_data.csv")

    # Deduplicate
    all_rows = deduplicate_windows(all_rows)
    log(f"After deduplication: {len(all_rows)} rows")

    # Group by ticker + acquisition date
    groups = defaultdict(list)
    for row in all_rows:
        key = (row["Ticker"], row["Acquisition Date"], row["Headline"])
        groups[key].append(row)

    # Analyse each group
    results = []
    for (ticker, acq_date, headline), rows in groups.items():
        company = rows[0]["Company"]

        # Role check — skip if company is not likely the acquirer
        if not is_likely_acquirer(company, headline):
            log(f"SKIPPED (not likely acquirer): {company} in '{headline[:60]}...'")
            continue

        movements = calculate_movements(rows)
        if not movements:
            log(f"SKIPPED (insufficient price data): {ticker} for {acq_date}")
            continue

        results.append({
            "company":          company,
            "ticker":           ticker,
            "headline":         headline,
            "acquisition_date": acq_date,
            "movements":        movements
        })
        log(f"ANALYSED: {ticker} — total change {movements['pct_change_total']}%")

    write_report(results)
    log(f"── Pattern analyser complete. {len(results)} events analysed. ──\n")

if __name__ == "__main__":
    run()
