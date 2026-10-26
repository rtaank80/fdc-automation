"""Approval email for the weekly Instagram post (standard library only).

Every button in the email is a link to the approval Worker (see worker/worker.js). Each link carries a
signed token:

    base64url(JSON payload) + "." + base64url(HMAC-SHA256(key, base64url(JSON payload)))

The Worker checks the signature and expiry, shows a confirmation page, and only acts when the approver
presses Confirm there (so mail scanners that merely open links cannot approve anything).

Payload keys: a = action (approve | skip | captions | image | all), d = draft id (GitHub run id),
c = chosen option number (approve only), i = chosen image letter (approve only), l = option label, w = ISO week, e = expiry (epoch seconds),
t = post topic, x = 1 when the draft is a dry run.
"""
from __future__ import annotations

import base64
import datetime as dt
import hashlib
import hmac
import html
import json
import smtplib
import ssl
from email.message import EmailMessage
from email.utils import formataddr, formatdate, make_msgid

CID = "postimage"  # each image gets its own id: postimage-a, postimage-b
TEAL = "#1f7a78"


def _b64u(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def sign_token(payload: dict, key: str) -> str:
    body = _b64u(json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8"))
    sig = _b64u(hmac.new(key.encode("utf-8"), body.encode("ascii"), hashlib.sha256).digest())
    return f"{body}.{sig}"


def make_link(base_url: str, payload: dict, key: str) -> str:
    return f"{base_url.rstrip('/')}/a/{sign_token(payload, key)}"


def _esc(s: str) -> str:
    return html.escape(s, quote=True)


def _expiry_text(exp: int) -> str:
    try:
        from zoneinfo import ZoneInfo

        when = dt.datetime.fromtimestamp(exp, ZoneInfo("Europe/London"))
    except Exception:  # noqa: BLE001  (no tz database available)
        when = dt.datetime.fromtimestamp(exp, dt.timezone.utc)
    return when.strftime("%A %d %B at %H:%M")


def _button(url: str, label: str, primary: bool = True) -> str:
    if primary:
        bg, fg, border = TEAL, "#ffffff", TEAL
    else:
        bg, fg, border = "#eef3f2", "#1b4a48", "#b9cfcc"
    return (
        '<table role="presentation" cellspacing="0" cellpadding="0" border="0" style="margin:6px 0;"><tr>'
        f'<td bgcolor="{bg}" style="border-radius:8px;">'
        f'<a href="{_esc(url)}" target="_blank" style="display:inline-block;padding:13px 22px;'
        f"font:600 15px Arial,Helvetica,sans-serif;color:{fg};text-decoration:none;border-radius:8px;"
        f'border:1px solid {border};">{_esc(label)}</a></td></tr></table>'
    )


def build_message(
    draft: dict,
    images: dict,
    *,
    base_url: str,
    key: str,
    to_addr: str,
    from_addr: str,
    draft_id: str,
    max_regens: int = 3,
    from_name: str = "FDC Instagram approvals",
) -> tuple[EmailMessage, str, str]:
    """Return (message, html, text). The html refers to the image as cid:postimage."""
    topic = draft.get("topic") or draft.get("theme", "this week's post")
    dry = bool(draft.get("dry_run"))
    exp = int(draft["expires_at"])
    left = max(0, max_regens - int(draft.get("regens_used", 0)))
    base = {"d": str(draft_id), "w": draft["week"], "e": exp, "t": topic[:120], "x": 1 if dry else 0}

    def link(action: str, **extra) -> str:
        return make_link(base_url, {**base, "a": action, **extra}, key)

    cands = draft["candidates"]
    letters = sorted(images)
    approve_links = {
        (c["number"], L): link("approve", c=c["number"], i=L, l=c["label"]) for c in cands for L in letters
    }
    many = len(letters) > 1
    others = [
        ("captions", "Generate new captions (images are fine)" if many else "Generate new captions (image is fine)"),
        ("image", "Generate new images (captions are fine)" if many else "Generate a new image (captions are fine)"),
        ("all", "Generate new images and new captions" if many else "Generate a new image and new captions"),
    ]
    other_links = {a: link(a) for a, _ in others}
    skip_link = link("skip")

    # ------------------------------------------------------------- html
    cards = []
    for c in cands:
        body = _esc(c["caption"]).replace("\n", "<br>")
        cards.append(
            '<table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0" '
            'style="margin:0 0 18px;border:1px solid #d9e2e0;border-radius:10px;"><tr><td style="padding:16px 18px;">'
            f'<div style="font:700 13px Arial,Helvetica,sans-serif;letter-spacing:.04em;text-transform:uppercase;'
            f'color:{TEAL};margin-bottom:8px;">Option {c["number"]} &middot; {_esc(c["label"])}</div>'
            f'<div style="font:15px/1.55 Georgia,\'Times New Roman\',serif;color:#23302f;">{body}</div>'
            f'<div style="font:12px Arial,Helvetica,sans-serif;color:#6b7b79;margin:10px 0 4px;">'
            f'Image description: {_esc(c["alt_text"])}</div>'
            + "".join(
                _button(approve_links[(c["number"], L)],
                        f"Approve option {c['number']} with image {L}" if many else f"Approve option {c['number']} and post")
                for L in letters
            )
            +
            "</td></tr></table>"
        )

    if left > 0:
        regen_html = "".join(_button(other_links[a], label, primary=False) for a, label in others)
        regen_note = f"Regenerations left for this draft: {left} of {max_regens}."
    else:
        regen_html = (
            '<p style="font:14px/1.5 Arial,Helvetica,sans-serif;color:#23302f;margin:6px 0;">'
            "No regenerations left for this draft. Approve one of the options above, or skip it.</p>"
        )
        regen_note = f"Regenerations left for this draft: 0 of {max_regens}."

    image_rows = "".join(
        '<tr><td style="padding:16px 24px 0;">'
        + (f'<div style="font:700 13px Arial,Helvetica,sans-serif;letter-spacing:.04em;text-transform:uppercase;'
           f'color:{TEAL};margin-bottom:6px;">Image {L}</div>' if many else "")
        + f'<img src="cid:{CID}-{L.lower()}" width="552" alt="{_esc(cands[0]["alt_text"])}" '
        'style="display:block;width:100%;max-width:552px;height:auto;border-radius:10px;"></td></tr>'
        for L in letters
    )

    banner = ""
    if dry:
        banner = (
            '<tr><td style="background:#fff4d6;padding:12px 24px;font:600 14px Arial,Helvetica,sans-serif;color:#6b4e00;">'
            "TEST RUN: placeholder image, nothing will be posted to Instagram.</td></tr>"
        )

    html_doc = f"""<!doctype html>
<html lang="en-GB"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="color-scheme" content="light"><meta name="supported-color-schemes" content="light">
<title>Approve this Instagram post</title></head>
<body style="margin:0;padding:0;background:#f3f5f4;">
<table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0" bgcolor="#f3f5f4"><tr><td align="center" style="padding:24px 12px;">
<table role="presentation" width="600" cellspacing="0" cellpadding="0" border="0" bgcolor="#ffffff" style="width:100%;max-width:600px;background:#ffffff;border-radius:12px;overflow:hidden;">
{banner}
<tr><td style="padding:24px 24px 8px;">
<div style="font:700 12px Arial,Helvetica,sans-serif;letter-spacing:.08em;text-transform:uppercase;color:{TEAL};">FDC Instagram &middot; approval needed</div>
<h1 style="font:700 22px/1.3 Georgia,'Times New Roman',serif;color:#23302f;margin:6px 0 4px;">{_esc(topic)}</h1>
<p style="font:14px/1.5 Arial,Helvetica,sans-serif;color:#5c6b69;margin:0;">Pick the caption and image you want, or ask for something different. Nothing is posted unless you approve. These links work until {_esc(_expiry_text(exp))}.</p>
</td></tr>
{image_rows}
<tr><td style="padding:12px 24px 0;">{"".join(cards)}</td></tr>
<tr><td style="padding:4px 24px 8px;">
<div style="border-top:1px solid #d9e2e0;padding-top:16px;font:700 15px Arial,Helvetica,sans-serif;color:#23302f;">Not quite right?</div>
{regen_html}
<p style="font:12px Arial,Helvetica,sans-serif;color:#6b7b79;margin:4px 0 10px;">{_esc(regen_note)}</p>
{_button(skip_link, "Skip this draft (post nothing)", primary=False)}
</td></tr>
<tr><td style="padding:12px 24px 24px;font:12px/1.5 Arial,Helvetica,sans-serif;color:#6b7b79;">
Each button opens a confirmation page first; nothing happens until you confirm there. Links can be used once.
Imagery is illustrative. Sent automatically by the FDC Instagram automation.
</td></tr>
</table></td></tr></table></body></html>"""

    # ------------------------------------------------------------- text
    lines = []
    if dry:
        lines += ["TEST RUN: placeholder image, nothing will be posted.", ""]
    lines += [f"Approve this Instagram post: {topic}", "",
              f"Links work until {_expiry_text(exp)}. Each opens a confirmation page first.", ""]
    for c in cands:
        lines += [f"--- Option {c['number']}: {c['label']} ---", c["caption"], "",
                  *[f"Approve option {c['number']} with image {L}: {approve_links[(c['number'], L)]}" for L in letters], ""]
    lines += ["--- Not quite right? ---"]
    if left > 0:
        for a, label in others:
            lines += [f"{label}: {other_links[a]}"]
        lines += [regen_note]
    else:
        lines += ["No regenerations left for this draft."]
    lines += [f"Skip this draft: {skip_link}"]
    text = "\n".join(lines)

    # ------------------------------------------------------------- message
    msg = EmailMessage()
    prefix = "[TEST] " if dry else ""
    msg["Subject"] = f"{prefix}Approve this Instagram post: {topic}"[:200]
    msg["From"] = formataddr((from_name, from_addr))
    msg["To"] = to_addr
    msg["Date"] = formatdate(localtime=False)
    msg["Message-ID"] = make_msgid(domain=from_addr.split("@")[-1])
    msg.set_content(text)
    msg.add_alternative(html_doc, subtype="html")
    for L in letters:
        msg.get_payload()[1].add_related(images[L], maintype="image", subtype="jpeg", cid=f"<{CID}-{L.lower()}>")
    return msg, html_doc, text


def preview_html(html_doc: str, images: dict) -> str:
    """The same email as a standalone file you can open in a browser (images embedded)."""
    for L, data in images.items():
        uri = "data:image/jpeg;base64," + base64.b64encode(data).decode("ascii")
        html_doc = html_doc.replace(f"cid:{CID}-{L.lower()}", uri)
    return html_doc


def send(msg: EmailMessage, host: str, port: int, user: str, password: str) -> None:
    ctx = ssl.create_default_context()
    try:
        if port == 465:
            with smtplib.SMTP_SSL(host, port, context=ctx, timeout=60) as s:
                s.login(user, password)
                s.send_message(msg)
        else:
            with smtplib.SMTP(host, port, timeout=60) as s:
                s.ehlo()
                s.starttls(context=ctx)
                s.ehlo()
                s.login(user, password)
                s.send_message(msg)
    except smtplib.SMTPAuthenticationError as exc:
        raise RuntimeError(
            f"The mail server rejected the login (code {exc.smtp_code}). Check SMTP_USER (use the full "
            "email address) and SMTP_PASSWORD."
        ) from None
    except (smtplib.SMTPException, OSError) as exc:
        raise RuntimeError(f"Could not send the email via {host}:{port}: {type(exc).__name__}: {exc}") from None
