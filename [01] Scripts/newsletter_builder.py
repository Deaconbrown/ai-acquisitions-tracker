"""



newsletter_builder.py



Senal AI — Weekly Newsletter Pipeline



--------------------------------------



Reads this week's stories from the acquisitions CSV on Google Drive,



fetches each article for context, sends everything to the Anthropic API



for Claude to write the newsletter, then saves the finished HTML page



ready for Netlify deployment.







Environment variables required:



  ANTHROPIC_API_KEY          — Anthropic API key



  PERSONAL_DRIVE_TOKEN_B64   — Base64-encoded Google Drive OAuth token



  BUTTONDOWN_API_KEY         — Buttondown API key for email delivery







Run locally:  python newsletter_builder.py



Run in CI:    triggered by .github/workflows/newsletter.yml every Sunday



"""







import os



import io



import re



import csv



import json



import base64



import pickle



import datetime



import requests



from pathlib import Path



from render_email_html import render_email_html



import argparse



import html as html_mod











# ---------------------------------------------------------------------------



# CONFIG



# ---------------------------------------------------------------------------







DRIVE_CSV_NAME   = "acquisitions.csv"



OUTPUT_DIR       = Path("newsletter")          # folder committed to GitHub



PUBLIC_DIR       = Path("public")



ISSUES_DIR       = PUBLIC_DIR / "issues"



GEMINI_MODEL     = "gemini-2.0-flash"



MAX_ARTICLE_CHARS = 3000                       # chars fetched per article



MIN_STORIES       = 1                          # skip run if fewer stories found



MAX_STORIES       = 10                         # cap passed to Claude per issue











# ---------------------------------------------------------------------------



# CLOUD PATH DETECTION



# ---------------------------------------------------------------------------







def is_cloud():



    """Returns True when running inside GitHub Actions."""



    return os.environ.get("GITHUB_ACTIONS") == "true"











# ---------------------------------------------------------------------------



# GOOGLE DRIVE — load token



# ---------------------------------------------------------------------------







def load_drive_token():



    """Load the Drive OAuth token from env (cloud) or local pickle (dev)."""



    if is_cloud():



        token_b64 = os.environ["PERSONAL_DRIVE_TOKEN_B64"]



        token_bytes = base64.b64decode(token_b64)



        return pickle.loads(token_bytes)



    else:



        token_path = Path.home() / ".claude" / "credentials" / "google_drive_token.pickle"



        with open(token_path, "rb") as f:



            return pickle.load(f)











def get_drive_headers(token):



    """Return auth headers for Drive API calls, refreshing token if needed."""



    from google.auth.transport.requests import Request



    if token.expired and token.refresh_token:



        token.refresh(Request())



    return {"Authorization": f"Bearer {token.token}"}











def download_csv_from_drive(headers):



    """Find and download acquisitions.csv from Google Drive."""



    search_url = (



        "https://www.googleapis.com/drive/v3/files"



        f"?q=name='{DRIVE_CSV_NAME}' and trashed=false"



        "&fields=files(id,name)"



    )



    resp = requests.get(search_url, headers=headers)



    resp.raise_for_status()



    files = resp.json().get("files", [])



    if not files:



        raise FileNotFoundError(f"{DRIVE_CSV_NAME} not found on Google Drive")







    file_id = files[0]["id"]



    download_url = f"https://www.googleapis.com/drive/v3/files/{file_id}?alt=media"



    resp = requests.get(download_url, headers=headers)



    resp.raise_for_status()



    return resp.text











# ---------------------------------------------------------------------------



# STORY SELECTION — this week's new stories



# ---------------------------------------------------------------------------







def get_this_weeks_stories(csv_text):



    """



    Return stories published in the last 7 days.



    CSV columns: Date Found, Headline, Summary, Source URL, Feed



    """



    reader = csv.DictReader(io.StringIO(csv_text))



    cutoff = datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None) - datetime.timedelta(days=7)



    stories = []







    for row in reader:



        raw_date = row.get("Date Found", "").strip()



        if not raw_date:



            continue



        try:



            pub_date = datetime.datetime.fromisoformat(raw_date)



        except ValueError:



            continue



        if pub_date >= cutoff:



            days_diff = (datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None) - pub_date).days



            if days_diff == 0:



                row["days_ago"] = "Today"



            elif days_diff == 1:



                row["days_ago"] = "1 day ago"



            else:



                row["days_ago"] = f"{days_diff} days ago"



            stories.append(row)







    stories.sort(key=lambda r: r.get("Date Found", ""), reverse=True)



    return stories











# ---------------------------------------------------------------------------



# ARTICLE FETCHER



# ---------------------------------------------------------------------------







def fetch_article_text(url):



    """



    Fetch the article at url and return up to MAX_ARTICLE_CHARS of plain text.



    Returns empty string on any failure — pipeline continues without it.



    """



    try:



        resp = requests.get(



            url,



            timeout=10,



            headers={"User-Agent": "SenalAI-NewsletterBot/1.0"}



        )



        resp.raise_for_status()



        # Strip HTML tags simply — good enough for context extraction



        text = re.sub(r"<[^>]+>", " ", resp.text)



        text = re.sub(r"\s+", " ", text).strip()



        text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", text)

        return text[:MAX_ARTICLE_CHARS]



    except Exception:



        return ""











# ---------------------------------------------------------------------------



# ANTHROPIC API — write the newsletter



# ---------------------------------------------------------------------------







def build_newsletter_prompt(stories, week_start, week_end):



    """Build the user prompt containing all stories for this week."""



    stories_block = ""



    for i, story in enumerate(stories, 1):



        article_text = fetch_article_text(story.get("Source URL", ""))



        stories_block += f"""



Story {i}:



Title: {story.get('Headline', '')}



URL: {story.get('Source URL', '')}



Source: {story.get('Feed', '')}



Date: {story.get('days_ago', '')}



Summary: {story.get('Summary', '')}



Article excerpt: {article_text if article_text else '(not available)'}



---"""







    return f"""You are writing issue {week_start.strftime('%Y-%W')} of Senal AI, a weekly newsletter covering AI company acquisitions.







Week covered: {week_start.strftime('%d %B %Y')} to {week_end.strftime('%d %B %Y')}







Here are this week's stories:



{stories_block}







Write the complete newsletter following this exact structure:







1. LEAD_TITLE: The title of the most significant story this week (one line only)



2. LEAD_SOURCE: Publication name and author name if known, otherwise just publication



3. LEAD_URL: The URL of the lead story



4. LEAD_BODY: One paragraph of original analysis on the lead story. Maximum 4 sentences. Explain why this deal matters, what it signals about the AI industry, and what the strategic logic is. This is your own commentary, not a summary of the article. Do not write more than one paragraph.



5. STORIES: For each remaining story provide:



   - STORY_TAG: One word category (Infrastructure, Talent, Data, Hardware, Tooling, Research, or Other)



   - STORY_TITLE: The story title



   - STORY_AUTHOR: Author name if known



   - STORY_SOURCE: Publication name



   - STORY_URL: The URL



   - STORY_DATE: Use the Date field provided above exactly as given. Do not calculate or rewrite it.



6. WATCH_BODY: One forward-looking paragraph identifying the pattern across this week's deals and what to watch in the weeks ahead.







Return your response as valid JSON matching this schema exactly:



{{



  "lead_title": "string",



  "lead_source": "string",



  "lead_author": "string or empty string",



  "lead_url": "string",



  "lead_body": "string (one paragraph only, no line breaks)",



  "stories": [



    {{



      "tag": "string",



      "title": "string",



      "author": "string or empty string",



      "source": "string",



      "url": "string",



      "date": "string"



    }}



  ],



  "watch_body": "string"



}}"""











SYSTEM_PROMPT = """You are the editor of Senal AI, a weekly newsletter covering AI company acquisitions.







Your writing rules:



- Write in clear, confident editorial prose aimed at investors, founders, and tech professionals.



- Every sentence must add information or insight. No filler, no padding.



- Short sentences. Plain words. No jargon unless it is the exact right word.



- Never use em dashes (the long dash like this: —) anywhere in your output. Use commas or full stops instead.



- Never use en dashes (–) anywhere in your output. Use commas or the word "to" instead.



- Do not use hyphens to join words unnecessarily.



- Never claim credit for the original reporting. The analysis is yours. The facts belong to the journalists who reported them.



- Always name the original publication and author when known.



- The lead_body field must be one paragraph only. Maximum 4 sentences. Never write more than one paragraph for lead_body. No double line breaks inside lead_body.



- Return only valid JSON. No preamble, no explanation, no markdown code fences."""











def call_gemini(prompt):

    api_key = os.environ.get("GEMINI_API_KEY", "")

    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent?key={api_key}"

    payload = {

        "system_instruction": {"parts": [{"text": SYSTEM_PROMPT}]},

        "contents": [{"parts": [{"text": prompt}]}],

        "generationConfig": {"maxOutputTokens": 4000}

    }

    resp = requests.post(url, headers={"Content-Type": "application/json"}, json=payload, timeout=120)

    if not resp.ok:

        print(f"Gemini error {resp.status_code}: {resp.text[:500]}")

    resp.raise_for_status()

    raw = resp.json()["candidates"][0]["content"]["parts"][0]["text"]

    raw = re.sub(r"—|–", "-", raw)

    return json.loads(raw)











def strip_dashes(obj):



    """



    Recursively remove em dashes and en dashes from all string values.



    Fallback safety net — the system prompt should prevent them appearing.



    """



    if isinstance(obj, str):



        obj = obj.replace("\u2014", ",")   # em dash to comma



        obj = obj.replace("\u2013", " to ") # en dash to "to"



        return obj



    if isinstance(obj, dict):



        return {k: strip_dashes(v) for k, v in obj.items()}



    if isinstance(obj, list):



        return [strip_dashes(i) for i in obj]



    return obj











# ---------------------------------------------------------------------------



# HTML GENERATOR



# ---------------------------------------------------------------------------







def render_html(data, issue_number, week_start, week_end):



    """Render the newsletter data into a full HTML page."""







    stories_html = ""



    for story in data.get("stories", []):



        author_line = f"{story['author']} · " if story.get("author") else ""



        stories_html += f"""



        <div class="sn-story-card">



          <div class="sn-story-tag">{story['tag']}</div>



          <div class="sn-story-title">{story['title']}</div>



          <div class="sn-story-byline">{author_line}<a href="{story['url']}" target="_blank" rel="noopener">{story['source']} →</a></div>



          <div class="sn-story-byline">{story['date']}</div>



        </div>"""







    lead_author_line = f"{data['lead_author']} · " if data.get("lead_author") else ""



    lead_body_html = "".join(



        f"<p>{para.strip()}</p>"



        for para in data["lead_body"].split("\n\n")



        if para.strip()



    )







    date_range = f"{week_start.strftime('%d %b')} to {week_end.strftime('%d %b %Y')}"







    return f"""<!DOCTYPE html>



<html lang="en">



<head>



  <meta charset="UTF-8">



  <meta name="viewport" content="width=device-width, initial-scale=1.0">



  <title>Senal AI, Issue {issue_number} · {date_range}</title>



  <meta name="description" content="Weekly AI acquisition intelligence. Issue {issue_number}, {date_range}.">



  <style>



    *, *::before, *::after {{ box-sizing: border-box; margin: 0; padding: 0; }}



    body {{ background: #0d0f0d; font-family: system-ui, -apple-system, sans-serif; color: #e8e8e8; }}







    .sn-nav {{ display: flex; align-items: center; justify-content: space-between; padding: 18px 40px; border-bottom: 1px solid #39ff1422; background: #0d0f0ddd; }}



    .sn-logo {{ font-size: 18px; font-weight: 700; letter-spacing: 0.18em; color: #39ff14; text-transform: uppercase; text-decoration: none; }}



    .sn-logo span {{ color: #e8e8e8; font-weight: 400; }}



    .sn-nav-links {{ display: flex; gap: 28px; font-size: 11px; letter-spacing: 0.12em; text-transform: uppercase; }}



    .sn-nav-links a {{ color: #666; text-decoration: none; }}



    .sn-nav-links a:hover {{ color: #39ff14; }}



    .sn-subscribe-btn {{ background: #39ff14; color: #0d0f0d; border: none; padding: 8px 18px; font-size: 11px; font-weight: 700; letter-spacing: 0.12em; text-transform: uppercase; cursor: pointer; border-radius: 2px; text-decoration: none; }}







    .sn-issue-header {{ max-width: 860px; margin: 0 auto; padding: 40px 40px 0; }}



    .sn-issue-meta {{ font-size: 10px; letter-spacing: 0.25em; color: #39ff14; text-transform: uppercase; margin-bottom: 10px; }}



    .sn-issue-title {{ font-size: 13px; color: #444; }}







    .sn-section {{ padding: 32px 40px; max-width: 860px; margin: 0 auto; }}



    .sn-section-label {{ font-size: 10px; letter-spacing: 0.25em; color: #39ff14; text-transform: uppercase; margin-bottom: 22px; padding-bottom: 10px; border-bottom: 1px solid #39ff1420; }}







    .sn-lead-card {{ background: #0f110f; border: 1px solid #39ff141a; border-left: 3px solid #39ff14; padding: 26px 30px; margin-bottom: 14px; border-radius: 2px; }}



    .sn-issue-tag {{ font-size: 10px; color: #39ff14; letter-spacing: 0.2em; text-transform: uppercase; margin-bottom: 10px; }}



    .sn-lead-title {{ font-size: 20px; font-weight: 600; color: #efefef; line-height: 1.35; margin-bottom: 6px; }}



    .sn-lead-source {{ font-size: 11px; color: #39ff1455; margin-bottom: 14px; }}



    .sn-lead-source a {{ color: #39ff1488; text-decoration: none; }}



    .sn-lead-source a:hover {{ color: #39ff14; }}



    .sn-lead-body p {{ font-size: 14px; color: #777; line-height: 1.8; margin-bottom: 14px; }}



    .sn-lead-body p:last-child {{ margin-bottom: 18px; }}



    .sn-read-link {{ font-size: 11px; color: #39ff14; letter-spacing: 0.12em; text-transform: uppercase; text-decoration: none; }}







    .sn-story-grid {{ display: grid; grid-template-columns: 1fr 1fr; gap: 10px; }}



    @media (max-width: 600px) {{ .sn-story-grid {{ grid-template-columns: 1fr; }} }}



    .sn-story-card {{ background: #0f110f; border: 1px solid #1a1e1a; padding: 16px 18px; border-radius: 2px; }}



    .sn-story-tag {{ font-size: 10px; color: #39ff1466; letter-spacing: 0.15em; text-transform: uppercase; margin-bottom: 6px; }}



    .sn-story-title {{ font-size: 13px; font-weight: 500; color: #bbb; line-height: 1.45; margin-bottom: 5px; }}



    .sn-story-byline {{ font-size: 11px; color: #3a3a3a; margin-bottom: 2px; }}



    .sn-story-byline a {{ color: #39ff1455; text-decoration: none; }}



    .sn-story-byline a:hover {{ color: #39ff14; }}







    .sn-watch {{ background: #0f110f; border: 1px solid #1a1e1a; border-top: 1px solid #39ff1430; padding: 22px 28px; border-radius: 2px; margin-top: 14px; }}



    .sn-watch-label {{ font-size: 10px; color: #39ff14; letter-spacing: 0.2em; text-transform: uppercase; margin-bottom: 8px; }}



    .sn-watch-body {{ font-size: 14px; color: #666; line-height: 1.8; }}







    .sn-kofi-strip {{ max-width: 860px; margin: 0 auto; padding: 0 40px 24px; }}



    .sn-kofi-inner {{ border: 1px solid #39ff1420; padding: 18px 26px; display: flex; align-items: center; justify-content: space-between; border-radius: 2px; background: #090b09; gap: 20px; }}



    .sn-kofi-text {{ font-size: 13px; color: #555; }}



    .sn-kofi-btn {{ background: transparent; border: 1px solid #39ff1455; color: #39ff14; padding: 8px 20px; font-size: 11px; letter-spacing: 0.12em; text-transform: uppercase; cursor: pointer; border-radius: 2px; font-weight: 600; text-decoration: none; white-space: nowrap; }}







    .sn-disclaimer {{ max-width: 860px; margin: 0 auto; padding: 20px 40px 24px; font-size: 11px; color: #2e2e2e; line-height: 1.8; border-top: 1px solid #111; }}



    .sn-footer {{ border-top: 1px solid #111; padding: 18px 40px; display: flex; align-items: center; justify-content: space-between; font-size: 10px; color: #2e2e2e; text-transform: uppercase; letter-spacing: 0.1em; max-width: 860px; margin: 0 auto; }}



  </style>



</head>



<body>







<nav class="sn-nav">



  <a class="sn-logo" href="/">SEÑAL<span> AI</span></a>



  <div class="sn-nav-links">



    <a href="/archive">Archive</a>



    <a href="/about">About</a>



  </div>



  <a class="sn-subscribe-btn" href="/#subscribe">Subscribe free</a>



</nav>







<div class="sn-issue-header">



  <div class="sn-issue-meta">Issue {issue_number} · {date_range}</div>



  <div class="sn-issue-title">AI acquisition intelligence, every Sunday</div>



</div>







<div class="sn-section">



  <div class="sn-section-label">Lead story</div>



  <div class="sn-lead-card">



    <div class="sn-issue-tag">This week's biggest deal</div>



    <div class="sn-lead-title">{data['lead_title']}</div>



    <div class="sn-lead-source">Reported by {lead_author_line}<a href="{data['lead_url']}" target="_blank" rel="noopener">{data['lead_source']} →</a></div>



    <div class="sn-lead-body">{lead_body_html}</div>



    <a class="sn-read-link" href="{data['lead_url']}" target="_blank" rel="noopener">Read original article →</a>



  </div>



</div>







<div class="sn-section">



  <div class="sn-section-label">The week's deals</div>



  <div class="sn-story-grid">



    {stories_html}



  </div>



  <div class="sn-watch">



    <div class="sn-watch-label">What to watch</div>



    <div class="sn-watch-body">{data['watch_body']}</div>



  </div>



</div>







<div class="sn-kofi-strip">



  <div class="sn-kofi-inner">



    <div class="sn-kofi-text">Senal AI is free. If it is useful, you can support it.</div>



    <a class="sn-kofi-btn" href="https://ko-fi.com/senaiai" target="_blank" rel="noopener">Support on Ko-fi</a>



  </div>



</div>







<div class="sn-disclaimer">



  Senal AI provides original analysis and commentary on news reported elsewhere. All source articles remain the property of their respective publishers and authors. Senal AI does not claim credit for any original reporting. Stories are linked directly to their source. This newsletter is independently produced and is not affiliated with any of the companies or publications mentioned.



</div>







<div class="sn-footer">



  <div>© 2026 Senal AI</div>



  <div>senalai.com · weekly AI acquisition intelligence</div>



  <a href="https://buttondown.com/senaiai/unsubscribe" style="color: #2e2e2e; text-decoration: none;">Unsubscribe</a>



</div>







</body>



</html>"""











# ---------------------------------------------------------------------------



# BUTTONDOWN — send email to subscribers



# ---------------------------------------------------------------------------







def send_to_buttondown(subject, html_body):



    """Publish the newsletter to Buttondown subscribers."""



    api_key = os.environ.get("BUTTONDOWN_API_KEY", "")



    if not api_key:



        print("BUTTONDOWN_API_KEY not set — skipping email delivery.")



        return







    resp = requests.post(



        "https://api.buttondown.email/v1/emails",



        headers={



            "Authorization": f"Token {api_key}",



            "Content-Type": "application/json",



            "X-Buttondown-Live-Dangerously": "true"



        },



        json={



            "subject": subject,



            "body": html_body,



            "status": "about_to_send"



        },



        timeout=30



    )



    if resp.status_code in (200, 201):



        print("Buttondown: email queued successfully.")



    else:



        print(f"Buttondown error {resp.status_code}: {resp.text}")











# ---------------------------------------------------------------------------



# PUBLIC SITE UPDATE — inject latest issue into static HTML



# ---------------------------------------------------------------------------







def _update_index_html(data, issue_number):



    """Replace the LATEST ISSUE PREVIEW block in public/index.html."""



    index_path = PUBLIC_DIR / "index.html"



    if not index_path.exists():



        print(f"Warning: {index_path} not found, skipping index update.")



        return







    content = index_path.read_text(encoding="utf-8")



    start_marker = "<!-- LATEST ISSUE PREVIEW -->"



    end_marker   = "<!-- END LATEST ISSUE PREVIEW -->"



    start_idx = content.find(start_marker)



    end_idx   = content.find(end_marker)



    if start_idx == -1 or end_idx == -1:



        print("Warning: LATEST ISSUE PREVIEW markers not found in index.html, skipping.")



        return







    cards = ""



    for s in data.get("stories", [])[:4]:



        a   = html_mod.escape(s.get("author", ""))



        src = html_mod.escape(s.get("source", ""))



        url = s.get("url", "#")



        byline = f"{a} · " if a else ""



        cards += (



            f'      <div class="sn-story-card">\n'



            f'        <div class="sn-story-tag">{html_mod.escape(s.get("tag",""))}</div>\n'



            f'        <div class="sn-story-title">{html_mod.escape(s.get("title",""))}</div>\n'



            f'        <div class="sn-story-byline">{byline}'



            f'<a href="{url}" target="_blank" rel="noopener">{src} →</a></div>\n'



            f'        <div class="sn-story-byline">{html_mod.escape(s.get("date",""))}</div>\n'



            f'      </div>\n'



        )







    lead_author = data.get("lead_author", "")



    author_str  = f"Reported by {html_mod.escape(lead_author)} · " if lead_author else ""







    new_section = (



        f'<!-- LATEST ISSUE PREVIEW -->\n'



        f'  <div class="sn-section">\n'



        f'    <div class="sn-section-label">\n'



        f'      <span>Latest edition, issue {issue_number}</span>\n'



        f'      <a href="/archive.html">View all issues →</a>\n'



        f'    </div>\n\n'



        f'    <div class="sn-lead-card">\n'



        f'      <div class="sn-issue-tag">Lead story</div>\n'



        f'      <div class="sn-lead-title">{html_mod.escape(data.get("lead_title",""))}</div>\n'



        f'      <div class="sn-lead-source">{author_str}'



        f'<a href="{data.get("lead_url","#")}" target="_blank" rel="noopener">'



        f'{html_mod.escape(data.get("lead_source",""))} →</a></div>\n'



        f'      <div class="sn-lead-body">{html_mod.escape(data.get("lead_body",""))}</div>\n'



        f'      <a class="sn-read-link" href="/issues/issue-{issue_number:03d}.html">Read full edition →</a>\n'



        f'    </div>\n\n'



        f'    <div class="sn-story-grid">\n'



        f'{cards}'



        f'    </div>\n\n'



        f'    <div class="sn-watch">\n'



        f'      <div class="sn-watch-label">What to watch</div>\n'



        f'      <div class="sn-watch-body">{html_mod.escape(data.get("watch_body",""))}</div>\n'



        f'    </div>\n'



        f'  </div>\n'



        f'  <!-- END LATEST ISSUE PREVIEW -->'



    )







    new_content = content[:start_idx] + new_section + content[end_idx + len(end_marker):]



    index_path.write_text(new_content, encoding="utf-8")



    print(f"Updated: {index_path}")











def _update_archive_html(data, issue_number, week_end):



    """Prepend new issue row and update stats in public/archive.html."""



    archive_path = PUBLIC_DIR / "archive.html"



    if not archive_path.exists():



        print(f"Warning: {archive_path} not found, skipping archive update.")



        return







    content = archive_path.read_text(encoding="utf-8")







    content = re.sub(



        r'(<div class="sn-stat-value" id="statIssues">)\d+(</div>)',



        rf'\g<1>{issue_number}\g<2>',



        content



    )



    m = re.search(r'<div class="sn-stat-value" id="statStories">(\d+)</div>', content)



    prev_stories = int(m.group(1)) if m else 0



    new_stories  = prev_stories + 1 + len(data.get("stories", []))



    content = re.sub(



        r'(<div class="sn-stat-value" id="statStories">)\d+(</div>)',



        rf'\g<1>{new_stories}\g<2>',



        content



    )







    seen_tags = []



    for s in data.get("stories", []):



        t = s.get("tag", "")



        if t and t not in seen_tags:



            seen_tags.append(t)



    seen_tags = seen_tags[:4]



    tags_attr = ",".join(seen_tags)



    tag_spans = "".join(



        f'            <span class="sn-tag">{html_mod.escape(t)}</span>\n'



        for t in seen_tags



    )







    lead_body = data.get("lead_body", "")



    summary   = html_mod.escape(lead_body[:200] + ("..." if len(lead_body) > 200 else ""))



    date_str  = str(week_end.day) + week_end.strftime(" %b %Y")







    new_row = (



        f'\n      <div class="sn-issue-row" data-tags="{tags_attr}"\n'



        f'           onclick="window.location=\'/issues/issue-{issue_number:03d}.html\'">\n'



        f'        <div class="sn-issue-num">\n'



        f'          <span>{issue_number}</span>\n'



        f'          Issue\n'



        f'        </div>\n'



        f'        <div class="sn-issue-row-content">\n'



        f'          <div class="sn-issue-row-date">{date_str}</div>\n'



        f'          <div class="sn-issue-row-title">{html_mod.escape(data.get("lead_title",""))}</div>\n'



        f'          <div class="sn-issue-row-summary">{summary}</div>\n'



        f'          <div class="sn-issue-row-tags">\n'



        f'{tag_spans}'



        f'          </div>\n'



        f'        </div>\n'



        f'        <div class="sn-issue-row-arrow">→</div>\n'



        f'      </div>\n'



    )







    year = week_end.year



    year_label_tag = f'<div class="sn-year-label">{year}</div>'



    if year_label_tag in content:



        content = content.replace(year_label_tag + "\n", year_label_tag + "\n" + new_row, 1)



    else:



        first_group = content.find('<div class="sn-year-group"')



        if first_group == -1:



            first_group = content.find('<div class="sn-empty"')



        if first_group == -1:



            print("Warning: could not find insertion point in archive.html")



        else:



            year_block = (



                f'    <div class="sn-year-group" data-year="{year}">\n'



                f'      <div class="sn-year-label">{year}</div>\n'



                f'{new_row}\n'



                f'    </div>\n\n'



            )



            content = content[:first_group] + year_block + content[first_group:]







    archive_path.write_text(content, encoding="utf-8")



    print(f"Updated: {archive_path}")











def update_public_pages(data, issue_number, week_end, update_archive=True):



    """Update public/index.html and public/archive.html with the new issue."""



    _update_index_html(data, issue_number)



    if update_archive:



        _update_archive_html(data, issue_number, week_end)











# ---------------------------------------------------------------------------



# ISSUE NUMBER — derive from how many issues exist already



# ---------------------------------------------------------------------------







def get_issue_number():



    ISSUES_DIR.mkdir(parents=True, exist_ok=True)



    existing = list(ISSUES_DIR.glob("issue-*.html"))



    return len(existing) + 1











# ---------------------------------------------------------------------------



# MAIN



# ---------------------------------------------------------------------------







def main(web_only=False):



    print("Senal AI newsletter builder starting...")







    week_end   = datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)



    week_start = week_end - datetime.timedelta(days=7)







    # 1. Load stories from Drive CSV



    print("Loading Drive token...")



    token   = load_drive_token()



    headers = get_drive_headers(token)







    print("Downloading acquisitions CSV from Drive...")



    csv_text = download_csv_from_drive(headers)







    print("Selecting this week's stories...")



    stories = get_this_weeks_stories(csv_text)



    print(f"Found {len(stories)} stories for this week.")







    if len(stories) < MIN_STORIES:



        print(f"Fewer than {MIN_STORIES} stories found. Skipping this week.")



        return







    stories = stories[:MAX_STORIES]



    print(f"Using top {len(stories)} stories for this issue.")







    # 2. Call Anthropic API



    print("Calling Anthropic API to write newsletter...")



    prompt = build_newsletter_prompt(stories, week_start, week_end)



    data   = call_gemini(prompt)



    print("Newsletter written.")







    # 3. Render HTML



    issue_number = get_issue_number()



    html = render_html(data, issue_number, week_start, week_end)



    email_html = render_email_html(data, issue_number, week_start, week_end)







    # 4. Save HTML file



    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)



    ISSUES_DIR.mkdir(parents=True, exist_ok=True)







    if not web_only:



        issue_filename = f"issue-{issue_number:03d}.html"



        issue_path     = ISSUES_DIR / issue_filename



        issue_path.write_text(html, encoding="utf-8")



        print(f"Saved: {issue_path}")







        newsletter_path = PUBLIC_DIR / "newsletter.html"



        newsletter_path.write_text(html, encoding="utf-8")



        print(f"Saved: {newsletter_path}")







    update_public_pages(data, issue_number, week_end, update_archive=not web_only)







    # Also write as latest.html for easy linking



    latest_path = PUBLIC_DIR / "latest.html"



    latest_path.write_text(html, encoding="utf-8")



    print(f"Saved: {latest_path}")







    # 5. Send to Buttondown



    subject = f"Señal AI · Issue {issue_number} · {week_start.strftime('%d %b')} to {week_end.strftime('%d %b %Y')}"



    if not web_only:



        print(f"Sending to Buttondown: {subject}")



        send_to_buttondown(subject, email_html)







    print("Done. Newsletter pipeline complete.")











if __name__ == "__main__":



    parser = argparse.ArgumentParser()



    parser.add_argument("--web-only", action="store_true", help="Update website only — skip issue save and Buttondown send")



    args = parser.parse_args()



    main(web_only=args.web_only)



