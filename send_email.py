#!/usr/bin/env python3
"""EdgeBot email sender — posts a markdown brief as a styled HTML email via Resend.

Env:
  RESEND_API_KEY   (required)  — Resend API key
  EDGEBOT_EMAIL_TO (optional)  — recipient (default kevin@suckiel.com)
  EDGEBOT_EMAIL_FROM (optional)— sender (default "EdgeBot <onboarding@resend.dev>",
                                 which Resend's free tier allows to your own account email)
Usage:
  python3 send_email.py --subject "EdgeBot NFL — Mon Nov 9" --file /tmp/edgebot/brief.md

Fail-safe: prints OK / NO_KEY / ERROR and exits non-zero on failure, but the
scheduled run has already delivered the brief by push regardless.
"""
from __future__ import annotations

import argparse
import html as _html
import os
import re
import sys

try:
    import requests
except Exception:
    requests = None

FROM = os.environ.get("EDGEBOT_EMAIL_FROM", "EdgeBot <noreply@edgebot.suckiel.com>")
# EDGEBOT_EMAIL_TO may be a comma/space/semicolon-separated list. The first is the
# visible To:, any others are BCC'd so recipients can't see each other / reply-all.
TO = os.environ.get("EDGEBOT_EMAIL_TO", "kevin@suckiel.com")


def _recipients(s):
    return [x.strip() for x in re.split(r"[,;\s]+", s or "") if x.strip()]


def md_to_html(md: str) -> str:
    """Minimal, safe markdown -> HTML (headings, bold, bullets, line breaks)."""
    out = []
    in_list = False
    for raw in md.splitlines():
        line = raw.rstrip()
        esc = _html.escape(line)
        # bold **x**
        esc = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", esc)
        if not line.strip():
            if in_list:
                out.append("</ul>")
                in_list = False
            out.append("<div style='height:8px'></div>")
            continue
        if line.startswith("# "):
            if in_list:
                out.append("</ul>"); in_list = False
            out.append(f"<h2 style='margin:14px 0 6px;font-size:18px'>{esc[2:]}</h2>")
        elif line.startswith("## "):
            if in_list:
                out.append("</ul>"); in_list = False
            out.append(f"<h3 style='margin:12px 0 4px;font-size:15px'>{esc[3:]}</h3>")
        elif re.match(r"\s*-\s+", line):
            if not in_list:
                out.append("<ul style='margin:4px 0 4px 18px;padding:0'>"); in_list = True
            item = re.sub(r"^\s*-\s+", "", esc)
            out.append(f"<li style='margin:2px 0'>{item}</li>")
        else:
            if in_list:
                out.append("</ul>"); in_list = False
            out.append(f"<p style='margin:4px 0'>{esc}</p>")
    if in_list:
        out.append("</ul>")
    body = "\n".join(out)
    return f"""<!doctype html><html><body style="margin:0;background:#f5f5f7;padding:16px">
<div style="max-width:640px;margin:0 auto;background:#fff;border-radius:12px;padding:20px 24px;
 font-family:-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;color:#1d1d1f;font-size:14px;line-height:1.5">
{body}
<hr style="border:none;border-top:1px solid #e5e5ea;margin:16px 0">
<p style="font-size:11px;color:#86868b;margin:0">EdgeBot · data points &amp; opinions for research only — not a placed wager or financial advice.</p>
</div></body></html>"""


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--subject", required=True)
    p.add_argument("--file", required=True)
    p.add_argument("--to", default=TO)
    a = p.parse_args()

    key = os.environ.get("RESEND_API_KEY")
    if not key:
        print("NO_KEY: RESEND_API_KEY not set — email skipped (brief still delivered by push).")
        return 2
    if requests is None:
        print("ERROR: requests not available"); return 2
    try:
        with open(a.file, encoding="utf-8") as f:
            md = f.read()
    except Exception as e:
        print(f"ERROR: cannot read {a.file}: {e}"); return 2

    rcpts = _recipients(a.to)
    if not rcpts:
        print("ERROR: no recipients"); return 2
    payload = {"from": FROM, "to": [rcpts[0]], "subject": a.subject, "html": md_to_html(md)}
    if len(rcpts) > 1:
        payload["bcc"] = rcpts[1:]
    try:
        r = requests.post(
            "https://api.resend.com/emails",
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
            json=payload, timeout=20)
        if r.status_code in (200, 201):
            print(f"OK: emailed {len(rcpts)} recipient(s) [{', '.join(rcpts)}] (id {r.json().get('id','?')})")
            return 0
        print(f"ERROR: Resend HTTP {r.status_code}: {r.text[:200]}")
        return 2
    except Exception as e:
        print(f"ERROR: {type(e).__name__}: {e}")
        return 2


if __name__ == "__main__":
    sys.exit(main())
