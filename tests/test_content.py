"""Tests for people selection, theme rotation, the seasonal calendar, per-image prompts/models and Cloudflare retries.

    python tests/test_content.py
"""
import collections, datetime as dt, io, base64, os, sys, tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT)); os.chdir(ROOT)
import post_weekly as pw
import requests
from PIL import Image

cfg = pw.load_config()
n = 0
def ok(msg):
    global n; n += 1; print(f"{n} ok: {msg}")

# ---- people
pool = cfg["people"]
assert abs(sum(g["weight"] for g in pool["groups"]) - 100) < 1e-9, "weights should add to 100"
c = collections.Counter()
for _ in range(6000):
    p = pw.pick_person(cfg)
    c["black" if any(k in p for k in ("Black", "Caribbean", "African")) else "white" if "white" in p else "asian"] += 1
share = {k: v / 6000 for k, v in c.items()}
assert 0.77 < share["white"] < 0.83 and 0.07 < share["black"] < 0.13 and 0.07 < share["asian"] < 0.13, share
ok(f"mix is about 80/10/10: {({k: round(v * 100) for k, v in share.items()})}")
prev = ["a man in his 30s of white British heritage"]
for _ in range(2000):
    a, b = pw.pick_people(cfg, 2, avoid=prev)
    assert a != b and a not in prev and b not in prev and a.split()[3] != b.split()[3]
ok("two people per draft always differ (including age band) and never repeat the previous draft's people")

# ---- themes
for t in cfg["themes"] + [x for es in cfg["calendar"]["months"].values() for x in es]:
    assert {"id", "topic", "angle", "image", "image_b"} <= t.keys(), t["id"]
    assert "{person}" in t["image"] and "{person}" in t["image_b"], f"{t['id']}: every image shows a person"
ids = [t["id"] for t in cfg["themes"]] + [x["id"] for es in cfg["calendar"]["months"].values() for x in es]
assert len(ids) == len(set(ids)), "theme ids must be unique"
ok(f"{len(ids)} themes, all with two person scenes and unique ids")

# ---- rotation
regular = {t["id"] for t in cfg["themes"]}
seen, seasonal = collections.Counter(), 0
for i in range(365):
    t = pw.pick_theme(cfg, dt.date(2026, 11, 1) + dt.timedelta(days=i))
    seen[t["id"]] += 1; seasonal += t["id"] not in regular
assert all(seen[i] >= 15 for i in regular), "every normal theme gets used"
assert 0.30 < seasonal / 365 < 0.38, seasonal
ok("a year of picks: all normal themes used, about one in three seasonal")
assert pw.pick_theme(cfg, dt.date(2027, 3, 19))["id"] == "world-oral-health-day" and pw.pick_theme(cfg, dt.date(2027, 3, 20))["id"] == "world-oral-health-day"
ok("a pinned theme takes every post in its window")
for m, es in cfg["calendar"]["months"].items():
    for t in es:
        assert pw.find_theme(cfg, t["id"]) is t
ok("find_theme finds seasonal themes (needed when regenerating a seasonal draft)")
os.environ["THEME_ID"] = "party-season-smile"; assert pw.pick_theme(cfg)["id"] == "party-season-smile"
os.environ["THEME_ID"] = "nope"
try: pw.pick_theme(cfg); raise AssertionError("should exit")
except SystemExit: pass
del os.environ["THEME_ID"]
ok("THEME_ID forces any theme and rejects unknown ones")

# ---- per-image prompts and models
os.environ.update(CLOUDFLARE_ACCOUNT_ID="acc", CLOUDFLARE_API_TOKEN="tok")
for k in ("IMAGE_MODEL_A", "IMAGE_MODEL_B", "IMAGE_MODEL"): os.environ.pop(k, None)
buf = io.BytesIO(); Image.new("RGB", (64, 64), (200, 50, 50)).save(buf, "PNG"); b64 = base64.b64encode(buf.getvalue()).decode()
calls = []
class R:
    status_code = 200; headers = {"content-type": "application/json"}; text = ""
    def json(self): return {"result": {"image": b64}, "success": True}
requests.post = lambda url, **kw: (calls.append((url, kw)), R())[1]
th = next(t for t in cfg["themes"] if t["id"] == "confidence")
out = Path(tempfile.mkdtemp())
pa = pw.generate_image(cfg, th, out / "a.jpg", False, person="a man in his 30s of white British heritage", letter="A")
pb = pw.generate_image(cfg, th, out / "b.jpg", False, person="a woman in her 50s of Indian heritage", letter="B")
assert "flux-2-dev" in calls[0][0] and "files" in calls[0][1], "A uses FLUX.2 (multipart)"
assert "lucid-origin" in calls[1][0] and "json" in calls[1][1], "B uses Lucid Origin (JSON)"
assert pa != pb and "man in his 30s" in pa and "woman in her 50s" in pb and "Bright, airy" in pb
assert "smiling or laughing" in pa and "everyday clothes" in pa
ok("image A and B use different models, scenes and styles; both get smile, skin and clothing wording")
os.environ["IMAGE_MODEL_B"] = "@cf/black-forest-labs/flux-1-schnell"; calls.clear()
pw.generate_image(cfg, th, out / "b.jpg", False, person="a woman in her 50s of Indian heritage", letter="B")
assert "flux-1-schnell" in calls[0][0] and "width" not in calls[0][1]["json"]
del os.environ["IMAGE_MODEL_B"]
ok("IMAGE_MODEL_B overrides the model")

# ---- Cloudflare retry on temporary errors
seq = [500, 500, 200]; sleeps = []
class Rx(R):
    def __init__(self, code): self.status_code = code; self.text = "boom"
requests.post = lambda url, **kw: Rx(seq.pop(0)) if seq[0] != 200 else R()
pw.time.sleep = lambda s: sleeps.append(s)
assert pw._cloudflare_image("p", "@cf/leonardo/lucid-origin")
assert sleeps == [15, 30]
ok("two temporary 500s are retried (waits 15s, 30s) and then succeed")
requests.post = lambda url, **kw: Rx(500); sleeps.clear()
try: pw._cloudflare_image("p", "@cf/leonardo/lucid-origin"); raise AssertionError("should exit")
except SystemExit: pass
assert len(sleeps) == 4
ok("five failures in a row give up after 4 waits")
requests.post = lambda url, **kw: Rx(400); sleeps.clear()
try: pw._cloudflare_image("p", "@cf/leonardo/lucid-origin")
except SystemExit: pass
assert sleeps == []
ok("a 400 error is not retried")
LIMIT_TEXT = ('{"errors":[{"message":"AiError: AiError: you have used up your daily free allocation of 10,000 neurons, '
              'please upgrade to Cloudflare\'s Workers Paid plan if you would like to continue usage. (x)","code":4006}],'
              '"success":false,"result":{},"messages":[]}')
class Rlim(R):
    status_code = 429; text = LIMIT_TEXT
requests.post = lambda url, **kw: Rlim(); sleeps.clear()
try: pw._cloudflare_image("p", "@cf/leonardo/lucid-origin"); raise AssertionError("should raise")
except pw.CloudflareLimitError as e: assert "daily free allowance" in str(e)
assert sleeps == []
ok("the 'daily allowance used up' 429 (code 4006) stops the quick retries straight away")
assert pw.is_limit_error(429, '{"code": 3036}') and not pw.is_limit_error(429, "Too many requests") \
    and not pw.is_limit_error(500, LIMIT_TEXT)
ok("an ordinary 429 is still treated as temporary")
