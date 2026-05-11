def render_email_html(data, issue_number, week_start, week_end):
    """
    Render the newsletter as email-safe HTML.
    Uses inline CSS and table layouts only — no style blocks, no CSS grid,
    no external fonts. Compatible with Gmail, Outlook, Apple Mail, and all
    major email clients.
    """

    date_range = f"{week_start.strftime('%d %b')} to {week_end.strftime('%d %b %Y')}"
    edition_day = "Monday" if week_start.weekday() == 0 else "Friday"

    # Lead story
    # Fix 4: fallback is empty string, not "Reported by " with no name
    lead_author_line = f"Reported by {data['lead_author']} · " if data.get("lead_author") else ""
    lead_body = data["lead_body"].split("\n\n")[0].strip()  # first paragraph only

    # Stories grid — pairs of two for table layout
    stories = data.get("stories", [])
    story_rows = []
    for i in range(0, len(stories), 2):
        pair = stories[i:i+2]
        story_rows.append(pair)

    def story_cell(story):
        author_line = f"{story['author']} · " if story.get("author") else ""
        return f"""
        <td width="50%" valign="top" style="padding: 0 5px 10px 5px;">
          <table width="100%" cellpadding="0" cellspacing="0" border="0"
                 style="background-color:#0f110f; border:1px solid #1a1e1a;">
            <tr>
              <td style="padding:14px 16px;">
                <p style="margin:0 0 5px 0; font-size:10px; color:rgba(57,255,20,0.40);
                           letter-spacing:0.12em; text-transform:uppercase;
                           font-family:Arial,Helvetica,sans-serif;">
                  {story['tag']}
                </p>
                <p style="margin:0 0 4px 0; font-size:12px; color:#bbbbbb;
                           line-height:1.4; font-family:Arial,Helvetica,sans-serif;">
                  {story['title']}
                </p>
                <p style="margin:0; font-size:11px; color:#555555;
                           font-family:Arial,Helvetica,sans-serif;">
                  {author_line}<a href="{story['url']}"
                    style="color:rgba(57,255,20,0.33); text-decoration:none;">{story['source']} &rarr;</a>
                  &nbsp;&middot;&nbsp;{story['date']}
                </p>
              </td>
            </tr>
          </table>
        </td>"""

    story_rows_html = ""
    for pair in story_rows:
        left = story_cell(pair[0])
        right = story_cell(pair[1]) if len(pair) > 1 else '<td width="50%" style="padding:0 5px 10px 5px;"></td>'
        story_rows_html += f"""
        <tr>
          {left}
          {right}
        </tr>"""

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Senal AI, Issue {issue_number} &middot; {date_range}</title>
</head>
<body style="margin:0; padding:0; background-color:#0d0f0d;">

<table width="100%" cellpadding="0" cellspacing="0" border="0"
       style="background-color:#0d0f0d;">
  <tr>
    <td align="center" style="padding:20px 0;">

      <!-- OUTER WRAPPER -->
      <table width="600" cellpadding="0" cellspacing="0" border="0"
             style="background-color:#0d0f0d; border:1px solid #1e221e;">

        <!-- HEADER -->
        <tr>
          <td style="background-color:#111411; padding:20px 32px 6px 32px;">
            <p style="margin:0 0 4px 0; font-size:15px; font-weight:700;
                       letter-spacing:0.15em; color:#39ff14; text-transform:uppercase;
                       font-family:Arial,Helvetica,sans-serif;">
              SE&Ntilde;AL <span style="color:#aaaaaa; font-weight:400;">AI</span>
            </p>
            <p style="margin:0 0 14px 0; font-size:10px; color:#555555;
                       letter-spacing:0.12em; text-transform:uppercase;
                       font-family:Arial,Helvetica,sans-serif;">
              Issue {issue_number} &nbsp;&middot;&nbsp; {week_end.strftime('%d %B %Y')} &nbsp;&middot;&nbsp; Twice weekly AI acquisition intelligence
            </p>
          </td>
        </tr>

        <!-- GREEN DIVIDER -->
        <tr>
          <td height="2" style="background-color:#39ff14; font-size:0; line-height:0;">&nbsp;</td>
        </tr>

        <!-- LEAD STORY -->
        <tr>
          <td style="padding:24px 32px; border-bottom:1px solid #1e221e;">
            <table width="100%" cellpadding="0" cellspacing="0" border="0"
                   style="background-color:#0f110f; border-left:3px solid #39ff14;">
              <tr>
                <td style="padding:20px 24px;">
                  <!-- Fix 3: table-cell badge instead of display:inline-block on <p> -->
                  <table cellpadding="0" cellspacing="0" border="0" style="margin:0 0 10px 0;">
                    <tr>
                      <td style="background-color:rgba(57,255,20,0.08); color:#39ff14;
                                 font-size:10px; letter-spacing:0.15em;
                                 text-transform:uppercase; padding:3px 8px;
                                 font-family:Arial,Helvetica,sans-serif;">
                        Lead story
                      </td>
                    </tr>
                  </table>
                  <p style="margin:0 0 6px 0; font-size:18px; font-weight:700;
                             color:#efefef; line-height:1.3;
                             font-family:Arial,Helvetica,sans-serif;">
                    {data['lead_title']}
                  </p>
                  <p style="margin:0 0 10px 0; font-size:11px; color:#555555;
                             font-family:Arial,Helvetica,sans-serif;">
                    {lead_author_line}<a href="{data['lead_url']}"
                      style="color:rgba(57,255,20,0.53); text-decoration:none;">
                      {data['lead_source']} &rarr;</a>
                  </p>
                  <p style="margin:0 0 14px 0; font-size:13px; color:#888888;
                             line-height:1.75; font-family:Arial,Helvetica,sans-serif;">
                    {lead_body}
                  </p>
                  <a href="{data['lead_url']}"
                     style="display:inline-block; background-color:#39ff14;
                            color:#0d0f0d; font-size:11px; font-weight:700;
                            letter-spacing:0.1em; text-transform:uppercase;
                            padding:8px 16px; text-decoration:none;
                            font-family:Arial,Helvetica,sans-serif;">
                    Read full story &rarr;
                  </a>
                </td>
              </tr>
            </table>
          </td>
        </tr>

        <!-- STORIES GRID -->
        <tr>
          <td style="padding:20px 32px; border-bottom:1px solid #1e221e;">
            <!-- Section label -->
            <table width="100%" cellpadding="0" cellspacing="0" border="0">
              <tr>
                <td style="padding-bottom:12px; border-bottom:1px solid #1e221e; margin-bottom:12px;">
                  <p style="margin:0; font-size:10px; color:#39ff14;
                             letter-spacing:0.2em; text-transform:uppercase;
                             font-family:Arial,Helvetica,sans-serif;">
                    The week's deals
                  </p>
                </td>
              </tr>
              <tr><td height="12" style="font-size:0; line-height:0;">&nbsp;</td></tr>
            </table>
            <!-- Story cards -->
            <table width="100%" cellpadding="0" cellspacing="0" border="0">
              {story_rows_html}
            </table>
          </td>
        </tr>

        <!-- WHAT TO WATCH -->
        <tr>
          <td style="padding:18px 32px; background-color:#0f110f;
                     border-bottom:1px solid #1e221e;">
            <p style="margin:0 0 6px 0; font-size:10px; color:#39ff14;
                       letter-spacing:0.2em; text-transform:uppercase;
                       font-family:Arial,Helvetica,sans-serif;">
              What to watch
            </p>
            <p style="margin:0; font-size:13px; color:#777777; line-height:1.75;
                       font-family:Arial,Helvetica,sans-serif;">
              {data['watch_body']}
            </p>
          </td>
        </tr>

        <!-- KO-FI -->
        <tr>
          <td style="padding:14px 32px; background-color:#090b09;
                     border-bottom:1px solid #1e221e;">
            <table width="100%" cellpadding="0" cellspacing="0" border="0">
              <tr>
                <td>
                  <p style="margin:0; font-size:12px; color:#555555;
                             font-family:Arial,Helvetica,sans-serif;">
                    Se&ntilde;al AI is free. If it is useful, you can support it.
                  </p>
                </td>
                <td align="right">
                  <a href="https://ko-fi.com/senaiai"
                     style="display:inline-block; border:1px solid rgba(57,255,20,0.27);
                            color:#39ff14; font-size:10px; letter-spacing:0.1em;
                            text-transform:uppercase; padding:6px 14px;
                            text-decoration:none;
                            font-family:Arial,Helvetica,sans-serif;">
                    Support on Ko-fi
                  </a>
                </td>
              </tr>
            </table>
          </td>
        </tr>

        <!-- DISCLAIMER -->
        <tr>
          <td style="padding:14px 32px; background-color:#090b09;
                     border-bottom:1px solid #111111;">
            <p style="margin:0; font-size:10px; color:#2e2e2e; line-height:1.7;
                       font-family:Arial,Helvetica,sans-serif;">
              Se&ntilde;al AI provides original analysis and commentary on news reported elsewhere.
              All source articles remain the property of their respective publishers and authors.
              Se&ntilde;al AI does not claim credit for any original reporting.
              This newsletter is independently produced and is not affiliated with any of the
              companies or publications mentioned.
            </p>
          </td>
        </tr>

        <!-- FOOTER -->
        <tr>
          <td style="padding:14px 32px; background-color:#090b09;">
            <table width="100%" cellpadding="0" cellspacing="0" border="0">
              <tr>
                <td>
                  <p style="margin:0; font-size:11px; color:#333333;
                             font-family:Arial,Helvetica,sans-serif;">
                    &copy; 2026 Se&ntilde;al AI &nbsp;&middot;&nbsp;
                    <a href="https://senalai.com"
                       style="color:#333333; text-decoration:none;">senalai.com</a>
                  </p>
                </td>
                <td align="right">
                  <a href="https://buttondown.com/senaiai/unsubscribe"
                     style="font-size:11px; color:#333333; text-decoration:none;
                            font-family:Arial,Helvetica,sans-serif;">
                    Unsubscribe
                  </a>
                </td>
              </tr>
            </table>
          </td>
        </tr>

      </table>
      <!-- END OUTER WRAPPER -->

    </td>
  </tr>
</table>

</body>
</html>"""
