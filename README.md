# AI Acquisitions Tracker

Monitors RSS news feeds for AI company acquisition stories.
Saves matches to Google Drive CSV. Runs automatically via GitHub Actions.

## Project Structure
- [01] Scripts/ — Python scraper
- [02] Data/ — CSV output (local only, not synced to GitHub)
- [03] Logs/ — Scraper logs (local only)
- [04] Config/ — Feed and keyword config
- [05] Reports/ — Analysis reports

## Stack
- Python 3.14
- GitHub Actions (scheduler)
- Google Drive API (data storage)
- Gmail API (notifications)
