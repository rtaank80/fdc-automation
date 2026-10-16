#!/usr/bin/env python3
"""Weekly Instagram post generator and publisher for FDC Cosmetics & Whitening.

Two stages so a human can approve in between:

    python post_weekly.py generate   -> writes out/draft.json + out/image.jpg
    python post_weekly.py publish    -> hosts the image and posts it to Instagram
    python post_weekly.py refresh    -> extends the Instagram token by 60 days

Add --dry-run to either stage to avoid any paid API call or real post.
Everything is configured by environment variables (see .env.example).
"""
from __future__ import annotations

import argparse
import base64
import datetime as dt
import io
import json
import os
import re
import sys
import time
from pathlib import Path

import requests

ROOT = Path(__file__).parent
OUT = ROOT / "out"
GRAPH = os.getenv("GRAPH_VERSION", "v23.0")
# Instagram API with Instagram Login: no Facebook Page needed.
IG_BASE = "https://graph.instagram.com"

# Words and phrases that must never appear in a dental advert (ASA/CAP and GDC style guidance).
BANNED = [
    "guarantee", "guaranteed", "best dentist", "cheapest", "painless", "risk-free",
    "100%", "miracle", "instant results", "permanent whitening", "cure", "no risk",
    "before and after", "before & after", "discount", "% off", "limited offer",
]


def log(msg: str) -> None:
    print(f"[{dt.datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)


def load_config() -> dict:
    return json.loads((ROOT / "themes.json").read_text(encoding="utf-8"))


def pick_theme(cfg: dict, today: dt.date | None = None) -> dict:
    """Rotate through themes by ISO week, or force one with THEME_ID."""
    forced = os.getenv("THEME_ID")
    themes = cfg["themes"]
    if forced:
        for t in themes:
            if t["id"] == forced:
                return t
        sys.exit(f"THEME_ID '{forced}' not found in themes.json")
    today = today or dt.date.today()
    year, week, _ = today.isocalendar()
    return themes[(year * 53 + week) % len(themes)]


# ---------------------------------------------------------------- caption
def fallback_caption(cfg: dict, theme: dict) -> dict:
    b = cfg["brand"]
    caption = (
        f"{theme['topic']}\n\n"
        f"{theme['angle']}. At Family Dental Centre in {b['location']}, we listen before we "
        f"recommend and shape a plan around you, with no pressure to decide.\n\n"
        f"Curious? Book a consultation via the link in our bio.\n"
        f"{b['website']}"
    )
    return {
        "caption": caption,
        "hashtags": b["base_hashtags"] + ["#TeethWhitening", "#Invisalign", "#SmileConfidence"],
        "alt_text": f"Illustrative image: {theme['topic']}",
    }


def llm_caption(cfg: dict, theme: dict) -> dict | None:
    key = os.getenv("ANTHROPIC_API_KEY")
    if not key:
        return None
    b = cfg["brand"]
    system = (
        "You write Instagram captions for a UK private dental practice. Follow UK dental "
        "advertising rules (ASA/CAP and GDC): no guarantees, no claims of best/cheapest/painless, "
        "no promised results, no prices or discounts, no before-and-after claims, no medical advice. "
        "Be warm, calm and informative. Encourage a consultation. Output ONLY valid JSON."
    )
    user = (
        f"Practice: {b['name']}, {b['location']}. Tone: {b['tone']}.\n"
        f"This week's topic: {theme['topic']}\nAngle: {theme['angle']}\n\n"
        "Return JSON with keys:\n"
        '  "caption": 450-900 characters, British English, a short hook line, 2-3 short paragraphs, '
        "a call to action to book a consultation via the link in bio, and the website URL "
        f"{b['website']} on its own line near the end. Max 2 emojis. Do NOT include hashtags.\n"
        '  "hashtags": array of 8-10 relevant hashtags (include #Birmingham)\n'
        '  "alt_text": one plain sentence (under 120 chars) describing an illustrative image of the topic\n'
    )
    try:
        r = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={"x-api-key": key, "anthropic-version": "2023-06-01", "content-type": "application/json"},
            json={
                "model": os.getenv("CAPTION_MODEL", "claude-sonnet-5-5"),
                "max_tokens": 900,
                "system": system,
                "messages": [{"role": "user", "content": user}],
            },
            timeout=90,
        )
        r.raise_for_status()
        text = "".join(p.get("text", "") for p in r.json()["content"])
        m = re.search(r"\{.*\}", text, re.S)
        data = json.loads(m.group(0))
        assert isinstance(data["caption"], str) and isinstance(data["hashtags"], list)
        data.setdefault("alt_text", f"Illustrative image: {theme['topic']}")
        return data
    except Exception as exc:  # noqa: BLE001
        log(f"Caption generation failed ({exc}); using fallback template.")
        return None


def compliant(text: str) -> bool:
    low = text.lower()
    hit = [w for w in BANNED if w in low]
    if hit:
        log(f"Caption rejected, contains banned wording: {hit}")
    return not hit


def build_caption(cfg: dict, theme: dict) -> dict:
    data = llm_caption(cfg, theme)
    if not data or not compliant(data["caption"]):
        data = fallback_caption(cfg, theme)
    b = cfg["brand"]
    tags = []
    for t in data["hashtags"] + b["base_hashtags"]:
        t = "#" + re.sub(r"[^0-9A-Za-z_]", "", t.lstrip("#"))
        if len(t) > 1 and t.lower() not in [x.lower() for x in tags]:
            tags.append(t)
    tags = tags[:12]
    full = f"{data['caption'].strip()}\n\n{b['disclaimer']}\n\n{' '.join(tags)}"
    if len(full) > 2150:  # Instagram limit is 2200
        full = full[:2147].rstrip() + "..."
    return {"caption": full, "alt_text": data["alt_text"][:300]}


# ---------------------------------------------------------------- image
def make_placeholder(theme: dict, path: Path) -> None:
    from PIL import Image, ImageDraw

    img = Image.new("RGB", (1080, 1080), (214, 238, 238))
    d = ImageDraw.Draw(img)
    d.rectangle([60, 60, 1020, 1020], outline=(40, 120, 120), width=6)
    d.text((100, 500), f"DRY RUN PLACEHOLDER\n{theme['topic']}", fill=(30, 90, 90))
    img.save(path, "JPEG", quality=92)


def generate_image(cfg: dict, theme: dict, path: Path, dry_run: bool) -> str:
    prompt = (
        f"{theme['image']}. Topic: {theme['topic']}. Style: {cfg['image_style']} "
        "Square composition, leave natural breathing room. Realistic, tasteful, not stock-photo cliché."
    )
    key = os.getenv("OPENAI_API_KEY")
    if dry_run or not key:
        if not dry_run:
            sys.exit("OPENAI_API_KEY is not set. Use --dry-run or set the key.")
        make_placeholder(theme, path)
        return prompt
    from PIL import Image

    r = requests.post(
        "https://api.openai.com/v1/images/generations",
        headers={"Authorization": f"Bearer {key}"},
        json={
            "model": os.getenv("IMAGE_MODEL", "gpt-image-1"),
            "prompt": prompt,
            "size": "1024x1024",
            "n": 1,
        },
        timeout=240,
    )
    if r.status_code >= 400:
        sys.exit(f"Image generation failed: {r.status_code} {r.text[:400]}")
    item = r.json()["data"][0]
    if "b64_json" in item:
        raw = base64.b64decode(item["b64_json"])
    else:
        raw = requests.get(item["url"], timeout=120).content
    # Instagram only accepts JPEG for feed images.
    Image.open(io.BytesIO(raw)).convert("RGB").save(path, "JPEG", quality=93)
    return prompt


def cmd_generate(args: argparse.Namespace) -> None:
    cfg = load_config()
    theme = pick_theme(cfg)
    OUT.mkdir(exist_ok=True)
    log(f"Theme this week: {theme['id']} ({theme['topic']})")
    cap = build_caption(cfg, theme)
    prompt = generate_image(cfg, theme, OUT / "image.jpg", args.dry_run)
    draft = {
        "theme": theme["id"],
        "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "image_prompt": prompt,
        **cap,
    }
    (OUT / "draft.json").write_text(json.dumps(draft, indent=2, ensure_ascii=False), encoding="utf-8")
    (OUT / "draft.md").write_text(
        f"# Draft post: {theme['topic']}\n\n**Alt text:** {cap['alt_text']}\n\n---\n\n{cap['caption']}\n",
        encoding="utf-8",
    )
    log("Draft written to out/ (draft.json, draft.md, image.jpg)")


# ---------------------------------------------------------------- hosting
def host_image(path: Path) -> str:
    """Instagram needs a public https URL for the image. Uses imgbb or a public GitHub repo."""
    mode = os.getenv("IMAGE_HOST", "imgbb")
    if mode == "imgbb":
        key = os.getenv("IMGBB_API_KEY") or sys.exit("IMGBB_API_KEY is not set")
        r = requests.post(
            "https://api.imgbb.com/1/upload",
            data={"key": key, "image": base64.b64encode(path.read_bytes()).decode(), "expiration": 2592000},
            timeout=120,
        )
        r.raise_for_status()
        return r.json()["data"]["url"]
    if mode == "github":
        # Requires a PUBLIC repo and a token with contents:write (GITHUB_TOKEN in Actions works).
        repo = os.environ["GITHUB_REPOSITORY"]
        token = os.environ["GITHUB_TOKEN"]
        name = f"images/{dt.date.today().isoformat()}.jpg"
        api = f"https://api.github.com/repos/{repo}/contents/{name}"
        h = {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"}
        existing = requests.get(api, headers=h, timeout=30)
        body = {"message": f"Add post image {name}", "content": base64.b64encode(path.read_bytes()).decode()}
        if existing.status_code == 200:
            body["sha"] = existing.json()["sha"]
        r = requests.put(api, headers=h, json=body, timeout=60)
        r.raise_for_status()
        url = f"https://raw.githubusercontent.com/{repo}/{os.getenv('GITHUB_REF_NAME', 'main')}/{name}"
        time.sleep(10)  # let the CDN pick it up
        return url
    sys.exit(f"Unknown IMAGE_HOST '{mode}'")


# ---------------------------------------------------------------- instagram
def graph(method: str, path: str, **params) -> dict:
    params["access_token"] = os.environ["IG_ACCESS_TOKEN"]
    r = requests.request(method, f"{IG_BASE}/{GRAPH}/{path}", params=params, timeout=90)
    if r.status_code >= 400:
        sys.exit(f"Instagram API error {r.status_code}: {r.text[:600]}")
    return r.json()


def publish_to_instagram(image_url: str, caption: str, alt_text: str) -> str:
    ig_id = os.getenv("IG_USER_ID") or "me"  # "me" resolves to the account that owns the token
    container = graph("POST", f"{ig_id}/media", image_url=image_url, caption=caption, alt_text=alt_text)["id"]
    log(f"Container created: {container}")
    for _ in range(30):
        status = graph("GET", container, fields="status_code").get("status_code")
        if status == "FINISHED":
            break
        if status in ("ERROR", "EXPIRED"):
            sys.exit(f"Container status {status}")
        time.sleep(4)
    else:
        sys.exit("Timed out waiting for Instagram to process the image")
    media_id = graph("POST", f"{ig_id}/media_publish", creation_id=container)["id"]
    return media_id


def cmd_publish(args: argparse.Namespace) -> None:
    draft = json.loads((OUT / "draft.json").read_text(encoding="utf-8"))
    image = OUT / "image.jpg"
    if args.dry_run:
        log("DRY RUN: would host image and publish this caption:")
        print("-" * 60 + f"\n{draft['caption']}\n" + "-" * 60)
        return
    if not os.getenv("IG_ACCESS_TOKEN"):
        sys.exit("IG_ACCESS_TOKEN is not set")
    url = host_image(image)
    log(f"Image hosted at {url}")
    media_id = publish_to_instagram(url, draft["caption"], draft["alt_text"])
    log(f"Published to Instagram. Media id: {media_id}")


def cmd_refresh(args: argparse.Namespace) -> None:
    """Exchange the long-lived token for a fresh 60-day one and write it to --out.

    Instagram only allows a refresh when the token is at least 24 hours old and not yet expired.
    Running this weekly keeps the token alive indefinitely.
    """
    if args.dry_run:
        log("DRY RUN: would refresh the Instagram token")
        return
    token = os.getenv("IG_ACCESS_TOKEN") or sys.exit("IG_ACCESS_TOKEN is not set")
    r = requests.get(
        f"{IG_BASE}/refresh_access_token",
        params={"grant_type": "ig_refresh_token", "access_token": token},
        timeout=60,
    )
    if r.status_code >= 400:
        # Not fatal: a brand-new token (<24h old) cannot be refreshed yet.
        print(f"::warning::Token refresh failed ({r.status_code}): {r.text[:300]}")
        return
    data = r.json()
    days = round(data.get("expires_in", 0) / 86400)
    log(f"Token refreshed; valid for about {days} more days")
    if args.out:
        Path(args.out).write_text(data["access_token"], encoding="utf-8")


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("stage", choices=["generate", "publish", "refresh"])
    p.add_argument("--dry-run", action="store_true", help="No paid API calls and no real post")
    p.add_argument("--out", help="refresh only: file to write the new token to")
    args = p.parse_args()
    {"generate": cmd_generate, "publish": cmd_publish, "refresh": cmd_refresh}[args.stage](args)


if __name__ == "__main__":
    main()
