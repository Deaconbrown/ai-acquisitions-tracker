"""
One-off utility: list Buttondown draft-status emails and delete all except
the one whose subject exactly matches KEEP_SUBJECT.

Run via GitHub Actions (uses the existing BUTTONDOWN_API_KEY secret) - not
part of the normal newsletter pipeline, safe to remove after use.
"""

import os
import requests

API_KEY = os.environ["BUTTONDOWN_API_KEY"]
KEEP_SUBJECT = os.environ.get("KEEP_SUBJECT", "")

HEADERS = {"Authorization": f"Token {API_KEY}"}

resp = requests.get(
    "https://api.buttondown.email/v1/emails",
    headers=HEADERS,
    params={"status": "draft"},
)
resp.raise_for_status()
drafts = resp.json().get("results", [])

print(f"Found {len(drafts)} draft(s):")
for d in drafts:
    print(f"  id={d['id']}  subject={d.get('subject', '')!r}  created={d.get('creation_date', '')}")

to_delete = [d for d in drafts if d.get("subject", "") != KEEP_SUBJECT]
to_keep = [d for d in drafts if d.get("subject", "") == KEEP_SUBJECT]

print(f"\nKeeping {len(to_keep)} matching subject {KEEP_SUBJECT!r}")
print(f"Deleting {len(to_delete)} other draft(s)...")

for d in to_delete:
    del_resp = requests.delete(
        f"https://api.buttondown.email/v1/emails/{d['id']}",
        headers=HEADERS,
    )
    status = "OK" if del_resp.status_code in (200, 202, 204) else f"FAILED ({del_resp.status_code}: {del_resp.text[:200]})"
    print(f"  Deleted id={d['id']} subject={d.get('subject', '')!r} - {status}")

print("\nDone.")
