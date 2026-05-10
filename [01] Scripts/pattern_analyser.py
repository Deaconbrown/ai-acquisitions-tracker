import csv
import os
import sys
import json
import base64
import requests
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

_SCRIPT_DIR   = os.path.dirname(os.path.abspath(__file__))
T212_MAP_FILE = os.path.join(_SCRIPT_DIR, "..", "[04] Config", "tickers_t212.json")

# Load .env file for local runs — stored outside synced folders for security
_env_path = os.path.expanduser(r"~\.claude\credentials\ai_acquisitions_tracker.env")
if os.path.exists(_env_path):
    with open(_env_path) as _f:
        for _line in _f:
            _line = _line.strip()
            if _line and not _line.startswith('#') and '=' in _line:
                _k, _v = _line.split('=', 1)
                os.environ.setdefault(_k.strip(), _v.strip())

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

# ── TRADING 212 PRICE FETCH ──────────────────────────────────────────────────
def fetch_t212_prices():
    """Calls T212 portfolio endpoint to get held positions, and loads ticker map
    for availability info. Returns dict keyed by yfinance ticker."""
    api_key    = os.environ.get("T212_API_KEY", "")
    api_secret = os.environ.get("T212_API_SECRET", "")

    if not api_key or not api_secret:
        log("  [T212] Credentials not set — skipping T212 section")
        return {}

    try:
        encoded = base64.b64encode(f'{api_key}:{api_secret}'.encode()).decode()
        headers = {'Authorization': f'Basic {encoded}'}

        if not os.path.exists(T212_MAP_FILE):
            log("  [T212] tickers_t212.json not found — skipping")
            return {}
        with open(T212_MAP_FILE, 'r') as f:
            t212_map = json.load(f)

        r = requests.get('https://live.trading212.com/api/v0/equity/portfolio',
                         headers=headers, timeout=10)
        if r.status_code != 200:
            log(f"  [T212] Portfolio fetch failed (HTTP {r.status_code}) — skipping")
            return {}

        held = {p['ticker']: p for p in r.json()}

        result = {}
        for yf_ticker, t212_ticker in t212_map.items():
            position = held.get(t212_ticker)
            if position:
                result[yf_ticker] = {
                    't212_ticker':   t212_ticker,
                    'current_price': position.get('currentPrice'),
                    'avg_price':     position.get('averagePrice'),
                    'quantity':      position.get('quantity'),
                    'ppl':           position.get('ppl'),
                    'held':          True,
                }
            else:
                result[yf_ticker] = {'t212_ticker': t212_ticker, 'held': False}

        held_count = sum(1 for v in result.values() if v.get('held'))
        log(f"  [T212] {held_count} tracked ticker(s) currently held on T212")
        return result

    except Exception as e:
        log(f"  [T212 ERROR] {e}")
        return {}

# ── REPORT WRITER ─────────────────────────────────────────────────────────────
def write_report(results, t212_data=None):
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

    # ── Trading 212 section ──
    if t212_data:
        lines.append("## Trading 212 — Tracked Ticker Status")
        lines.append(f"**Checked:** {datetime.now().strftime('%Y-%m-%d %H:%M')}")
        lines.append("")
        lines.append("| Ticker | T212 Available | Held | Qty | Avg Price | Current Price | P&L |")
        lines.append("|:-------|:--------------|:-----|:----|:----------|:-------------|:----|")
        for yf_ticker, data in sorted(t212_data.items()):
            if data.get('held'):
                qty   = round(data['quantity'], 4)
                avg   = round(data['avg_price'], 4)
                cur   = round(data['current_price'], 4)
                ppl   = round(data['ppl'], 2)
                lines.append(f"| {yf_ticker} | ✅ | ✅ Held | {qty} | ${avg} | ${cur} | {'+' if ppl >= 0 else ''}{ppl} |")
            else:
                lines.append(f"| {yf_ticker} | ✅ | — | — | — | — | — |")
        lines.append("")
        lines.append(f"*34 of 35 tracked tickers available on T212. SSNLF (Samsung OTC) not listed.*")
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

    t212_data = fetch_t212_prices()
    write_report(results, t212_data)
    log(f"── Pattern analyser complete. {len(results)} events analysed. ──\n")

if __name__ == "__main__":
    run()
