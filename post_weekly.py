#!/usr/bin/env python3
"""Weekly Instagram post for FDC Cosmetics & Whitening.

Stages:

    python post_weekly.py generate   -> builds a draft (up to 3 captions + an image) and emails the approver
    python post_weekly.py publish    -> posts the approved option from a draft (started by the approval Worker)
    python post_weekly.py refresh    -> extends the Instagram token by 60 days

Add --dry-run to avoid paid API calls (generate) or a real post (publish).
Everything is configured by environment variables (see .env.example).
"""
from __future__ import annotations

import argparse
import base64
import datetime as dt
import io
import json
import os
import random
import re
import shutil
import sys
import time
import zipfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import requests

import approval_email

ROOT = Path(__file__).parent
OUT = ROOT / "out"
GRAPH = os.getenv("GRAPH_VERSION", "v25.0")
# Instagram API with Instagram Login: no Facebook Page needed.
IG_BASE = "https://graph.instagram.com"
DRAFT_DAYS = 5  # the draft and its email links expire after this many days
MAX_REGENS = 3  # regenerations allowed per draft (enforced by the Worker, shown in the email)

# Words and phrases that must never appear in a dental advert (ASA/CAP and GDC style guidance).
BANNED = [
    "guarantee", "guaranteed", "best dentist", "cheapest", "painless", "risk-free",
    "100%", "miracle", "instant results", "permanent whitening", "cure", "no risk",
    "before and after", "before & after", "discount", "% off", "limited offer",
]

LETTERS = ["A", "B"]  # the images offered in each draft

VARIATIONS = [
    "Try a different camera angle and framing.",
    "Use a different colour balance and lighting mood.",
    "Use a noticeably different composition.",
    "Change the setting and background while keeping the same idea.",
]


def log(msg: str) -> None:
    print(f"[{dt.datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)


def load_config() -> dict:
    return json.loads((ROOT / "themes.json").read_text(encoding="utf-8"))


def iso_week(today: dt.date | None = None) -> str:
    year, week, _ = (today or dt.date.today()).isocalendar()
    return f"{year}-W{week:02d}"


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


def _prompts(cfg: dict, theme: dict, avoid: list[str] | None = None) -> tuple[str, str]:
    """The instructions sent to the AI models (Claude and Cloudflare share them)."""
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
    if avoid:
        shown = "\n".join(f"- {a[:300]!r}" for a in avoid)
        user += (
            "\nThese captions were already offered and not chosen. Write something clearly different "
            f"in its opening line, structure and wording:\n{shown}\n"
        )
    return system, user


def _parse_caption_json(text: str, theme: dict) -> dict:
    """Read the JSON a model returned. Models often put raw line breaks inside strings, or use code fences."""
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        raise ValueError(f"no JSON object in model output, which began: {text[:200]!r}")
    try:
        data = json.loads(m.group(0), strict=False)  # strict=False allows raw newlines inside strings
    except json.JSONDecodeError as exc:
        raise ValueError(f"{exc}; model output began: {text[:300]!r}") from exc
    if not isinstance(data.get("caption"), str) or not data["caption"].strip():
        raise ValueError(f"no usable 'caption' in model output: {str(data)[:200]}")
    tags = data.get("hashtags", [])
    if isinstance(tags, str):  # some models return "#a #b #c" instead of a list
        tags = re.findall(r"#\w+", tags)
    data["hashtags"] = [t for t in tags if isinstance(t, str)]
    data.setdefault("alt_text", f"Illustrative image: {theme['topic']}")
    if not isinstance(data["alt_text"], str):
        data["alt_text"] = f"Illustrative image: {theme['topic']}"
    return data


def claude_caption(cfg: dict, theme: dict, avoid: list[str] | None = None) -> dict | None:
    key = os.getenv("ANTHROPIC_API_KEY")
    if not key:
        return None
    system, user = _prompts(cfg, theme, avoid)
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
        return _parse_caption_json("".join(p.get("text", "") for p in r.json()["content"]), theme)
    except Exception as exc:  # noqa: BLE001
        log(f"Claude caption failed ({exc}); skipping that option.")
        return None


def cloudflare_caption(cfg: dict, theme: dict, avoid: list[str] | None = None) -> dict | None:
    account = os.getenv("CLOUDFLARE_ACCOUNT_ID")
    token = os.getenv("CLOUDFLARE_API_TOKEN")
    if not (account and token):
        return None
    system, user = _prompts(cfg, theme, avoid)
    model = os.getenv("CAPTION_CF_MODEL", "@cf/meta/llama-3.3-70b-instruct-fp8-fast")
    try:
        r = requests.post(
            f"https://api.cloudflare.com/client/v4/accounts/{account}/ai/run/{model}",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
                "max_tokens": 900,
            },
            timeout=120,
        )
        r.raise_for_status()
        result = r.json().get("result", {})
        # Older models answer {"response": "..."}; newer ones use the OpenAI-style "choices" shape.
        text = result.get("response")
        if text is None and result.get("choices"):
            text = result["choices"][0]["message"]["content"]
        if not isinstance(text, str):
            raise ValueError(f"unexpected response shape: {str(result)[:200]}")
        return _parse_caption_json(text, theme)
    except Exception as exc:  # noqa: BLE001
        log(f"Cloudflare caption failed ({exc}); skipping that option.")
        return None


def compliant(text: str, label: str = "") -> bool:
    low = text.lower()
    hit = [w for w in BANNED if w in low]
    if hit:
        log(f"{label} caption rejected, contains banned wording: {hit}")
    return not hit


def finalise(cfg: dict, data: dict) -> dict:
    """Add hashtags and the disclaimer, and respect Instagram's length limit."""
    b = cfg["brand"]
    tags: list[str] = []
    for t in data["hashtags"] + b["base_hashtags"]:
        t = "#" + re.sub(r"[^0-9A-Za-z_]", "", t.lstrip("#"))
        if len(t) > 1 and t.lower() not in [x.lower() for x in tags]:
            tags.append(t)
    tags = tags[:12]
    full = f"{data['caption'].strip()}\n\n{b['disclaimer']}\n\n{' '.join(tags)}"
    if len(full) > 2150:  # Instagram limit is 2200
        full = full[:2147].rstrip() + "..."
    return {"caption": full, "alt_text": data["alt_text"][:300]}


# Fixed option numbers, so "2" always means Claude whichever options are available that week.
OPTIONS = [(1, "template", "Default template"), (2, "claude", "Claude"), (3, "cloudflare", "Cloudflare")]


def build_candidates(cfg: dict, theme: dict, dry_run: bool = False, avoid: list[str] | None = None) -> list[dict]:
    makers = {
        "template": lambda: fallback_caption(cfg, theme),
        "claude": lambda: None if dry_run else claude_caption(cfg, theme, avoid),
        "cloudflare": lambda: None if dry_run else cloudflare_caption(cfg, theme, avoid),
    }
    out = []
    for num, cid, label in OPTIONS:
        data = makers[cid]()
        if not data:
            continue
        if not compliant(data["caption"], label):
            continue
        out.append({"number": num, "id": cid, "label": label, **finalise(cfg, data)})
    return out


# ---------------------------------------------------------------- image
def make_placeholder(theme: dict, path: Path, label: str = "") -> None:
    from PIL import Image, ImageDraw

    img = Image.new("RGB", (1080, 1080), (214, 238, 238))
    d = ImageDraw.Draw(img)
    d.rectangle([60, 60, 1020, 1020], outline=(40, 120, 120), width=6)
    d.text((100, 500), f"DRY RUN PLACEHOLDER {label}\n{theme['topic']}", fill=(30, 90, 90))
    img.save(path, "JPEG", quality=92)


def _openai_image(prompt: str) -> bytes:
    key = os.getenv("OPENAI_API_KEY") or sys.exit("OPENAI_API_KEY is not set.")
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
        sys.exit(f"OpenAI image generation failed: {r.status_code} {r.text[:400]}")
    item = r.json()["data"][0]
    if "b64_json" in item:
        return base64.b64decode(item["b64_json"])
    return requests.get(item["url"], timeout=120).content


def _cloudflare_image(prompt: str, model: str) -> bytes:
    """Cloudflare Workers AI REST API. FLUX.2 models take multipart/form-data; the others take JSON."""
    account = os.getenv("CLOUDFLARE_ACCOUNT_ID") or sys.exit("CLOUDFLARE_ACCOUNT_ID is not set.")
    token = os.getenv("CLOUDFLARE_API_TOKEN") or sys.exit("CLOUDFLARE_API_TOKEN is not set.")
    fields = {"prompt": prompt, "width": "1024", "height": "1024", "seed": str(random.randint(1, 2**31 - 1))}
    # Cloudflare sometimes answers with a temporary 5xx (error 3043 "Internal server error") or 429.
    # Retrying after a pause normally works, so try up to 5 times before giving up.
    waits = [15, 30, 60, 120]
    for attempt in range(len(waits) + 1):
        try:
            url = f"https://api.cloudflare.com/client/v4/accounts/{account}/ai/run/{model}"
            headers = {"Authorization": f"Bearer {token}"}
            if "flux-2" in model:
                r = requests.post(url, headers=headers, timeout=240,
                                  files={k: (None, v) for k, v in fields.items()})  # multipart/form-data
            else:  # e.g. Leonardo Lucid Origin, FLUX.1 schnell: plain JSON
                body = {"prompt": prompt, "seed": int(fields["seed"])}
                if "flux-1" not in model:
                    body.update(width=1024, height=1024)
                r = requests.post(url, headers=headers, json=body, timeout=240)
        except (requests.Timeout, requests.ConnectionError) as e:
            r = None
            err = str(e)[:200]
        else:
            err = f"{r.status_code} {r.text[:200]}"
        temporary = r is None or r.status_code >= 500 or r.status_code == 429
        if not temporary or attempt == len(waits):
            break
        print(f"Cloudflare image attempt {attempt + 1} failed ({err}); retrying in {waits[attempt]}s", flush=True)
        time.sleep(waits[attempt])
    if r is None:
        sys.exit(f"Cloudflare image generation failed: {err}")
    if r.status_code >= 400:
        sys.exit(f"Cloudflare image generation failed: {r.status_code} {r.text[:500]}")
    ctype = r.headers.get("content-type", "")
    if ctype.startswith("image/"):
        return r.content
    body = r.json()
    result = body.get("result", body)
    b64 = result.get("image") if isinstance(result, dict) else None
    if not b64:
        sys.exit(f"Cloudflare response had no image field: {str(body)[:500]}")
    return base64.b64decode(b64)


def pick_person(cfg: dict, avoid=None, avoid_age: str | None = None) -> str | None:
    """A random person description such as 'a woman in her 50s of Indian heritage'.

    Image models drift towards one look unless told who to draw, so we choose explicitly each time.
    `avoid` is a description (or a collection of them) that must not be repeated; `avoid_age` is an
    age band such as "40s" that must not be used.
    """
    pool = cfg.get("people")
    if not pool:
        return None
    avoid = {avoid} if isinstance(avoid, str) else set(avoid or [])
    groups = pool.get("groups")
    person = None
    for _ in range(60):
        if groups:  # weighted: e.g. 80% white, 10% Black, 10% Asian
            group = random.choices(groups, weights=[g["weight"] for g in groups])[0]
            background = random.choice(group["backgrounds"])
        else:
            background = random.choice(pool["backgrounds"])
        gender = random.choice(pool["genders"])
        pronoun = "her" if gender == "woman" else "his"
        age = random.choice(pool["ages"])
        person = f"a {gender} in {pronoun} {age} {background}"
        if person not in avoid and age != avoid_age:
            break
    return person


def pick_people(cfg: dict, n: int, avoid=None) -> list[str | None]:
    """n different people for one draft's images: all different, each in a different age band,
    and none the same as the people in the draft being replaced."""
    avoid = set(a for a in (avoid or []) if a)
    chosen: list[str | None] = []
    ages: set[str] = set()
    for _ in range(n):
        p = pick_person(cfg, avoid=avoid | set(chosen))
        for _ in range(60):  # nudge towards a different age band than the other images
            if p is None or p.split(" ")[3] not in ages:
                break
            p = pick_person(cfg, avoid=avoid | set(chosen))
        chosen.append(p)
        if p:
            ages.add(p.split(" ")[3])
    return chosen


def generate_image(
    cfg: dict, theme: dict, path: Path, dry_run: bool, variation: str = "", person: str | None = None,
    label: str = "", letter: str = "A",
) -> str:
    template = theme.get("image_b") if letter == "B" and theme.get("image_b") else theme["image"]
    scene = template.replace("{person}", person or "an adult") if "{person}" in template else f"{person or 'An adult'}, {template}"
    styles = cfg.get("image_styles") or {}
    style = styles.get(letter) or styles.get("A") or cfg.get("image_style", "")
    clothing = (cfg.get("people") or {}).get("clothing")
    look = (cfg.get("people") or {}).get("look")
    if clothing:
        scene += f", {clothing}"
    if look:
        scene += f", {look}"
    prompt = (
        f"{scene}. Topic: {theme['topic']}. Style: {style} "
        "Square composition, leave natural breathing room. Realistic, tasteful, not stock-photo cliché. "
        "Any person is an authentic, respectful, natural-looking adult, never a caricature."
    )
    if variation:  # makes the images in one draft (and a regenerated image) look different from each other
        prompt += " " + variation
    if dry_run:
        make_placeholder(theme, path, label)
        return prompt
    from PIL import Image

    provider = os.getenv("IMAGE_PROVIDER", "cloudflare").lower()
    if provider == "cloudflare":
        models = cfg.get("models") or {}
        model = (os.getenv(f"IMAGE_MODEL_{letter}") or models.get(letter)
                 or os.getenv("IMAGE_MODEL") or "@cf/black-forest-labs/flux-2-klein-9b")
        raw = _cloudflare_image(prompt, model)
    elif provider == "openai":
        raw = _openai_image(prompt)
    else:
        sys.exit(f"Unknown IMAGE_PROVIDER '{provider}' (use 'cloudflare' or 'openai')")
    log(f"Image {letter} generated with {provider}" + (f" ({model})" if provider == "cloudflare" else ""))
    # Instagram only accepts JPEG for feed images.
    img = Image.open(io.BytesIO(raw)).convert("RGB")
    if img.size != (1080, 1080):
        img = img.resize((1080, 1080), Image.LANCZOS)
    img.save(path, "JPEG", quality=93)
    return prompt


# ---------------------------------------------------------------- drafts (kept as workflow artifacts)
def fetch_draft(run_id: str, dest: Path) -> dict:
    """Download the 'draft' artifact of an earlier workflow run into dest and return its draft.json."""
    repo = os.getenv("GITHUB_REPOSITORY") or sys.exit("GITHUB_REPOSITORY is not set (this runs inside GitHub Actions).")
    token = os.getenv("GITHUB_TOKEN") or sys.exit("GITHUB_TOKEN is not set.")
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"}
    r = requests.get(
        f"https://api.github.com/repos/{repo}/actions/runs/{run_id}/artifacts",
        headers=headers, params={"name": "draft"}, timeout=30,
    )
    r.raise_for_status()
    found = [a for a in r.json().get("artifacts", []) if not a.get("expired")]
    if not found:
        sys.exit(f"No unexpired 'draft' artifact found for run {run_id}.")
    z = requests.get(found[0]["archive_download_url"], headers=headers, timeout=120)
    z.raise_for_status()
    dest.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(io.BytesIO(z.content)) as zf:
        zf.extractall(dest)
    return json.loads((dest / "draft.json").read_text(encoding="utf-8"))


def draft_markdown(draft: dict) -> str:
    when = dt.datetime.fromtimestamp(draft["expires_at"], dt.timezone.utc).strftime("%Y-%m-%d %H:%M")
    lines = [
        f"# Draft: {draft['topic']}", "",
        f"Mode: {draft['mode']} | regenerations used for this draft: {draft['regens_used']} | expires {when} UTC", "",
    ]
    for im in prev_images(draft, OUT):
        lines += [f"Image {im['id']}: {im.get('person') or 'no person'}"]
    lines += [""]
    for c in draft["candidates"]:
        lines += [f"## Option {c['number']}: {c['label']}", "", c["caption"], "", "---", ""]
    return "\n".join(lines)


def prev_images(draft: dict, folder: Path) -> list[dict]:
    """The images listed in a draft (drafts made before two-image support have a single image.jpg)."""
    return draft.get("images") or [{"id": "A", "file": "image.jpg", "person": draft.get("person"),
                                    "prompt": draft.get("image_prompt", "")}]


def send_approval_email(draft: dict, images: dict, dry_run: bool) -> None:
    needed = ["APPROVAL_BASE_URL", "LINK_SECRET", "APPROVER_EMAIL", "SMTP_HOST", "SMTP_USER", "SMTP_PASSWORD"]
    missing = [n for n in needed if not os.getenv(n)]
    if missing:
        text = "Approval email NOT sent; missing settings: " + ", ".join(missing)
        if dry_run:
            log(text + " (fine for a dry run)")
            return
        sys.exit(text)
    msg, html_doc, _ = approval_email.build_message(
        draft,
        images,
        base_url=os.environ["APPROVAL_BASE_URL"],
        key=os.environ["LINK_SECRET"],
        to_addr=os.environ["APPROVER_EMAIL"],
        from_addr=os.getenv("MAIL_FROM") or os.environ["SMTP_USER"],
        draft_id=os.getenv("DRAFT_RUN_ID") or "local",
        max_regens=MAX_REGENS,
    )
    (OUT / "email-preview.html").write_text(approval_email.preview_html(html_doc, images), encoding="utf-8")
    try:
        approval_email.send(
            msg, os.environ["SMTP_HOST"], int(os.getenv("SMTP_PORT") or 587),
            os.environ["SMTP_USER"], os.environ["SMTP_PASSWORD"],
        )
    except RuntimeError as exc:
        sys.exit(str(exc))
    log("Approval email sent.")


def cmd_generate(args: argparse.Namespace) -> None:
    cfg = load_config()
    mode = (os.getenv("MODE") or "new").strip().lower()
    if mode not in ("new", "captions", "image", "all"):
        sys.exit(f"Unknown MODE '{mode}' (use new, captions, image or all)")
    regens_used = int(os.getenv("REGENS_USED") or 0)
    OUT.mkdir(exist_ok=True)

    prev = None
    prev_dir = ROOT / "previous"
    if mode == "new":
        theme = pick_theme(cfg)
    else:
        prev_id = os.getenv("PREV_RUN_ID") or sys.exit("This MODE needs PREV_RUN_ID (run ID of the draft being redone).")
        prev = fetch_draft(prev_id, prev_dir)
        theme = next((t for t in cfg["themes"] if t["id"] == prev["theme"]), None)
        if theme is None:
            sys.exit(f"Theme '{prev['theme']}' is no longer in themes.json")
    log(f"Mode '{mode}', theme: {theme['id']} ({theme['topic']})")

    if mode in ("new", "captions", "all"):
        avoid = [c["caption"] for c in prev["candidates"]] if prev else None
        candidates = build_candidates(cfg, theme, args.dry_run, avoid)
        if not candidates:
            sys.exit("No caption passed the compliance check.")
    else:  # image only: keep the captions the approver already saw
        candidates = prev["candidates"]
    log("Caption options: " + ", ".join(f"{c['number']}={c['label']}" for c in candidates))

    # Two images per draft so the approver can choose. Each has its own randomly chosen person.
    if mode == "captions":  # keep the existing images (and so the same people)
        images = prev_images(prev, prev_dir)
        for im in images:
            shutil.copyfile(prev_dir / im["file"], OUT / im["file"])
    else:
        prev_people = [im.get("person") for im in (prev or {}).get("images", [])]
        people = pick_people(cfg, len(LETTERS), avoid=prev_people)
        regen = mode != "new"  # a regenerated image also gets a random twist so it differs from the old one
        phrases = random.sample(VARIATIONS, len(LETTERS)) if regen else [""] * len(LETTERS)

        def make(i: int) -> dict | None:
            letter = LETTERS[i]
            fname = f"image-{letter.lower()}.jpg"
            person = people[i]  # every image shows a person
            log(f"Person in image {letter}: {person}")
            try:
                prompt = generate_image(cfg, theme, OUT / fname, args.dry_run, variation=phrases[i],
                                        person=person, label=f"IMAGE {letter}", letter=letter)
            except SystemExit as exc:  # one image failing should not lose the other
                log(f"WARNING: image {letter} failed ({exc}); continuing without it.")
                return None
            return {"id": letter, "file": fname, "person": person, "prompt": prompt}

        with ThreadPoolExecutor(max_workers=len(LETTERS)) as pool:  # both images at the same time
            images = [im for im in pool.map(make, range(len(LETTERS))) if im]
        if not images:
            sys.exit("Neither image could be generated.")

    draft = {
        # Groups a scheduled run with its regenerations (the Worker's limits apply per group, not per week).
        "week": prev["week"] if prev else f"{iso_week()}-{os.getenv('DRAFT_RUN_ID') or 'local'}",
        "theme": theme["id"],
        "topic": theme["topic"],
        "mode": mode,
        "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "expires_at": int(time.time()) + DRAFT_DAYS * 86400,
        "regens_used": regens_used,
        "dry_run": bool(args.dry_run),
        "images": images,
        "candidates": candidates,
    }
    (OUT / "draft.json").write_text(json.dumps(draft, indent=2, ensure_ascii=False), encoding="utf-8")
    (OUT / "draft.md").write_text(draft_markdown(draft), encoding="utf-8")
    log("Draft written to out/")
    send_approval_email(draft, {im["id"]: (OUT / im["file"]).read_bytes() for im in images}, args.dry_run)


# ---------------------------------------------------------------- hosting
def host_image(path: Path) -> str:
    """Instagram needs a public https URL for the image. Uses a public GitHub repo (default) or imgbb."""
    mode = os.getenv("IMAGE_HOST", "github")
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
        # Commits the image into the repo (images/ folder). The repo must be PUBLIC so Instagram can fetch it.
        # In Actions, GITHUB_REPOSITORY and GITHUB_TOKEN are provided automatically (needs contents: write).
        repo = os.environ["GITHUB_REPOSITORY"]
        token = os.environ["GITHUB_TOKEN"]
        name = f"images/{dt.date.today().isoformat()}.jpg"
        api = f"https://api.github.com/repos/{repo}/contents/{name}"
        h = {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"}
        body = {"message": f"Add post image {name}", "content": base64.b64encode(path.read_bytes()).decode()}
        existing = requests.get(api, headers=h, timeout=30)
        if existing.status_code == 200:  # re-run on the same day: overwrite
            body["sha"] = existing.json()["sha"]
        r = requests.put(api, headers=h, json=body, timeout=60)
        if r.status_code >= 400:
            sys.exit(f"Could not save image to GitHub: {r.status_code} {r.text[:400]}")
        url = r.json()["content"]["download_url"]
        # Fail early (with a clear message) if the repo is private and Instagram could not fetch it.
        for _ in range(6):
            if requests.head(url, timeout=30).status_code == 200:
                return url
            time.sleep(5)
        sys.exit(f"Image saved but is not publicly reachable at {url}. Is the repository public?")
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
    run_id = os.getenv("DRAFT_RUN_ID")
    if run_id:
        draft = fetch_draft(run_id, OUT)
    else:  # local use: a draft already in out/
        draft = json.loads((OUT / "draft.json").read_text(encoding="utf-8"))
    if int(draft["expires_at"]) < time.time():
        sys.exit("This draft has expired, so nothing was posted. Wait for the next weekly draft.")
    number = (os.getenv("CHOSEN_OPTION") or "").strip()
    chosen = next((c for c in draft["candidates"] if str(c["number"]) == number), None)
    if chosen is None:
        sys.exit(f"Option '{number}' is not in this draft (available: {[c['number'] for c in draft['candidates']]}).")
    letter = (os.getenv("CHOSEN_IMAGE") or "A").strip().upper()
    image = next((im for im in prev_images(draft, OUT) if im["id"] == letter), None)
    if image is None:
        sys.exit(f"Image '{letter}' is not in this draft (available: {[im['id'] for im in prev_images(draft, OUT)]}).")
    log(f"Approved option {chosen['number']} ({chosen['label']}) with image {letter}")
    # A test draft (placeholder image) must never reach Instagram, whatever flags were passed.
    if args.dry_run or draft.get("dry_run"):
        log("DRY RUN: would host the image and publish this caption:")
        print("-" * 60 + f"\n{chosen['caption']}\n" + "-" * 60)
        return
    if not os.getenv("IG_ACCESS_TOKEN"):
        sys.exit("IG_ACCESS_TOKEN is not set")
    url = host_image(OUT / image["file"])
    log(f"Image hosted at {url}")
    media_id = publish_to_instagram(url, chosen["caption"], chosen["alt_text"])
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
    p.add_argument("--dry-run", action="store_true", help="No paid API calls (generate) or no real post (publish)")
    p.add_argument("--out", help="refresh only: file to write the new token to")
    args = p.parse_args()
    {"generate": cmd_generate, "publish": cmd_publish, "refresh": cmd_refresh}[args.stage](args)


if __name__ == "__main__":
    main()
