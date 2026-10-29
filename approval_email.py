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
import re
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


def _button(url: str, label: str, primary: bool = True, width: int = 552) -> str:
    """A full-width button that also works in Outlook (VML) and has a thumb-sized tap area."""
    if primary:
        bg, fg, border = TEAL, "#ffffff", TEAL
    else:
        bg, fg, border = "#eef3f2", "#1b4a48", "#b9cfcc"
    u, lab = _esc(url), _esc(label)
    return (
        '<table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0" style="margin:5px 0;"><tr><td>'
        "<!--[if mso]>"
        f'<v:roundrect xmlns:v="urn:schemas-microsoft-com:vml" xmlns:w="urn:schemas-microsoft-com:office:word" '
        f'href="{u}" style="height:44px;v-text-anchor:middle;width:{width}px;" arcsize="18%" '
        f'strokecolor="{border}" fillcolor="{bg}"><w:anchorlock/>'
        f'<center style="color:{fg};font-family:Arial,Helvetica,sans-serif;font-size:15px;font-weight:bold;">{lab}</center>'
        "</v:roundrect><![endif]-->"
        "<!--[if !mso]><!-- -->"
        f'<a href="{u}" target="_blank" style="display:block;background:{bg};border:1px solid {border};'
        f"border-radius:8px;color:{fg};font:600 15px/42px Arial,Helvetica,sans-serif;text-align:center;"
        f'text-decoration:none;">{lab}</a>'
        "<!--<![endif]-->"
        "</td></tr></table>"
    )


def _weekday(draft: dict) -> str | None:
    try:
        from zoneinfo import ZoneInfo

        when = dt.datetime.fromisoformat(draft["generated_at"]).astimezone(ZoneInfo("Europe/London"))
        return when.strftime("%A")
    except Exception:  # noqa: BLE001
        return None


def _caption_html(caption: str, cut: int = 125) -> str:
    """The caption as Instagram shows it: hashtags in blue, and a marker where '... more' would cut it off."""
    text = caption
    if len(text) > cut:
        i = text.rfind(" ", 0, cut)
        i = i if i > 60 else cut
        head, tail = text[:i], text[i:]
    else:
        head, tail = text, ""

    def fmt(t: str) -> str:
        t = _esc(t).replace("\n", "<br>")
        return re.sub(r"(#\w+)", r'<span style="color:#00376b;">\1</span>', t)

    marker = (
        '<span style="display:inline-block;background:#efefef;color:#8e8e8e;font:11px Arial,Helvetica,sans-serif;'
        'padding:1px 6px;border-radius:9px;">&hellip; more</span>'
    )
    return fmt(head) + (marker + fmt(tail) if tail else "")


def _preheader(text: str) -> str:
    pad = "&#847;&zwnj;&nbsp;" * 70  # stops the email body leaking into the inbox preview line
    return (
        '<div style="display:none;max-height:0;overflow:hidden;mso-hide:all;font-size:1px;line-height:1px;'
        f'color:#f3f5f4;opacity:0;">{_esc(text)}{pad}</div>'
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
    account: str = "fdc.dental",
) -> tuple[EmailMessage, str, str]:
    """Return (message, html, text). The html refers to the images as cid:postimage-a, cid:postimage-b."""
    topic = draft.get("topic") or draft.get("theme", "this week's post")
    dry = bool(draft.get("dry_run"))
    exp = int(draft["expires_at"])
    left = max(0, max_regens - int(draft.get("regens_used", 0)))
    base = {"d": str(draft_id), "w": draft["week"], "e": exp, "t": topic[:120], "x": 1 if dry else 0}

    def link(action: str, **extra) -> str:
        return make_link(base_url, {**base, "a": action, **extra}, key)

    cands = draft["candidates"]
    letters = sorted(images)
    many = len(letters) > 1
    approve_links = {
        (c["number"], L): link("approve", c=c["number"], i=L, l=c["label"]) for c in cands for L in letters
    }
    others = [
        ("captions", "Generate new captions (images are fine)" if many else "Generate new captions (image is fine)"),
        ("image", "Generate new images (captions are fine)" if many else "Generate a new image (captions are fine)"),
        ("all", "Generate new images and new captions" if many else "Generate a new image and new captions"),
    ]
    other_links = {a: link(a) for a, _ in others}
    skip_link = link("skip")
    n_cap = len(cands)
    n_img = len(letters)
    expiry = _expiry_text(exp)
    summary = (
        f"{n_cap} caption{'s' if n_cap != 1 else ''} &middot; {n_img} image{'s' if n_img != 1 else ''} "
        f"&middot; link expires {_esc(expiry)}"
    )
    pre = f"{n_cap} caption{'s' if n_cap != 1 else ''} and {n_img} image{'s' if n_img != 1 else ''} waiting for your approval. Expires {expiry}."

    # ------------------------------------------------------------- html
    half = 252
    image_cells = "".join(
        '<td width="50%" valign="top" style="padding:0 4px;">'
        f'<div style="font:700 12px Arial,Helvetica,sans-serif;letter-spacing:.06em;text-transform:uppercase;color:{TEAL};margin:0 0 6px;">'
        f"{'Image ' + L if many else 'Image'}</div>"
        f'<img src="cid:{CID}-{L.lower()}" width="{half if many else 552}" alt="Image {L}" '
        'style="display:block;width:100%;height:auto;border-radius:10px;border:0;"></td>'
        for L in letters
    )
    image_row = (
        '<tr><td style="padding:14px 20px 0;"><table role="presentation" width="100%" cellspacing="0" cellpadding="0" '
        f'border="0"><tr>{image_cells}</tr></table></td></tr>'
    )

    cards = []
    for c in cands:
        btn_cells = "".join(
            f'<td width="{100 // n_img}%" valign="top" style="padding:0 {4 if many else 0}px;">'
            + _button(
                approve_links[(c["number"], L)],
                f"Post with image {L}" if many else "Approve and post",
                width=(half if many else 516),
            )
            + "</td>"
            for L in letters
        )
        cards.append(
            '<table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0" style="margin:0 0 20px;">'
            f'<tr><td style="font:700 12px Arial,Helvetica,sans-serif;letter-spacing:.06em;text-transform:uppercase;color:{TEAL};padding:0 2px 6px;">'
            f'Option {c["number"]} &middot; {_esc(c["label"])}</td></tr>'
            '<tr><td style="border:1px solid #dbdbdb;border-radius:12px;background:#ffffff;">'
            # mock Instagram post header
            '<table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0"><tr>'
            f'<td width="52" style="padding:12px 0 8px 14px;"><table role="presentation" cellspacing="0" cellpadding="0" border="0"><tr>'
            f'<td width="34" height="34" align="center" bgcolor="{TEAL}" style="border-radius:17px;color:#ffffff;'
            f'font:700 12px Arial,Helvetica,sans-serif;">FDC</td></tr></table></td>'
            f'<td style="padding:12px 14px 8px 10px;font:600 14px Arial,Helvetica,sans-serif;color:#262626;">{_esc(account)}'
            '<div style="font:400 11px Arial,Helvetica,sans-serif;color:#8e8e8e;">How this caption will look</div></td>'
            "</tr></table>"
            f'<div style="padding:2px 16px 14px;font:14px/1.5 Arial,Helvetica,sans-serif;color:#262626;">'
            f'<span style="font-weight:600;">{_esc(account)}</span> {_caption_html(c["caption"])}</div>'
            f'<div style="padding:0 16px 12px;font:11px/1.4 Arial,Helvetica,sans-serif;color:#8e8e8e;">'
            f'Image description: {_esc(c["alt_text"])}</div>'
            "</td></tr>"
            f'<tr><td style="padding-top:8px;"><table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0"><tr>{btn_cells}</tr></table></td></tr>'
            "</table>"
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

    banner = ""
    if dry:
        banner = (
            '<tr><td style="background:#fff4d6;padding:12px 24px;font:600 14px Arial,Helvetica,sans-serif;color:#6b4e00;">'
            "TEST RUN: placeholder images, nothing will be posted to Instagram.</td></tr>"
        )

    html_doc = f"""<!doctype html>
<html lang="en-GB" xmlns:v="urn:schemas-microsoft-com:vml" xmlns:o="urn:schemas-microsoft-com:office:office"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="color-scheme" content="light"><meta name="supported-color-schemes" content="light">
<!--[if mso]><xml><o:OfficeDocumentSettings><o:PixelsPerInch>96</o:PixelsPerInch></o:OfficeDocumentSettings></xml><![endif]-->
<title>Approve this Instagram post</title></head>
<body style="margin:0;padding:0;background:#f3f5f4;">
{_preheader(pre)}
<table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0" bgcolor="#f3f5f4"><tr><td align="center" style="padding:20px 10px;">
<table role="presentation" width="600" cellspacing="0" cellpadding="0" border="0" bgcolor="#ffffff" style="width:100%;max-width:600px;background:#ffffff;border-radius:14px;overflow:hidden;">
{banner}
<tr><td style="padding:24px 24px 14px;">
<div style="font:700 12px Arial,Helvetica,sans-serif;letter-spacing:.08em;text-transform:uppercase;color:{TEAL};">FDC Instagram &middot; approval needed</div>
<h1 style="font:700 24px/1.25 Georgia,'Times New Roman',serif;color:#23302f;margin:8px 0 10px;">{_esc(topic)}</h1>
<div style="font:600 13px/1.4 Arial,Helvetica,sans-serif;color:#1b4a48;background:#eef3f2;border-radius:8px;padding:9px 12px;">{summary}</div>
</td></tr>
{image_row}
<tr><td style="padding:20px 20px 0;">
<div style="font:14px/1.5 Arial,Helvetica,sans-serif;color:#5c6b69;margin:0 4px 14px;">{'Choose a caption, then the image to post it with.' if many else 'Choose a caption to post.'} Nothing is posted unless you approve.</div>
{"".join(cards)}</td></tr>
<tr><td style="padding:0 24px 8px;">
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
        lines += ["TEST RUN: placeholder images, nothing will be posted.", ""]
    lines += [f"Approve this Instagram post: {topic}", "",
              f"{n_cap} captions, {n_img} images. Links work until {expiry}. Each opens a confirmation page first.", ""]
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
    day = _weekday(draft)
    msg["Subject"] = f"{prefix}Approve {day + chr(39) + 's' if day else 'this'} post: {topic}"[:200]
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
