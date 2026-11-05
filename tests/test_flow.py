import sys, os, json, re, shutil, time, argparse
from pathlib import Path
"""End-to-end test of generate / email / publish with every external service replaced by a stub.

Runs against a temporary copy of the repo, so nothing in your working tree is touched.
    python tests/test_flow.py
"""
import tempfile
SRC = Path(__file__).resolve().parent.parent
PROJ = str(Path(tempfile.mkdtemp()) / "proj")
shutil.copytree(SRC, PROJ, ignore=shutil.ignore_patterns(".git", "node_modules", "__pycache__", "out", "previous"))
sys.path.insert(0, PROJ); os.chdir(PROJ)
os.environ.update(APPROVAL_BASE_URL="https://fdc-approvals.example.workers.dev", LINK_SECRET="test-secret-123",
    APPROVER_EMAIL="approver@example.com", SMTP_HOST="smtp.example", SMTP_USER="sender@example.com",
    SMTP_PASSWORD="pw", DRAFT_RUN_ID="9001")
import approval_email, post_weekly as pw
real_host_image, real_archive = pw.host_image, pw.archive_image   # tests 7-9 stub hosting; 15 tests the real ones
from email import message_from_bytes, policy

sent = []
approval_email.send = lambda msg, host, port, user, pwd: sent.append((msg, host, port, user))
good = lambda n: {"caption": f"Hook {n}.\n\nBody text {n}.\n\nBook via link in bio.\nhttps://x", "hashtags": ["#a", "#Birmingham"], "alt_text": f"alt {n}"}
pw.cloudflare_caption = lambda cfg, theme, avoid=None: good("cf" + ("-avoid" if avoid else ""))
img_calls = []
def fake_img(cfg, theme, path, dry, variation="", person=None, label="", letter="A"):
    img_calls.append((variation, person)); pw.make_placeholder(theme, path, label); return "prompt"
pw.generate_image = fake_img
ns = lambda dry: argparse.Namespace(dry_run=dry, out=None)
def links(msg):
    html = msg.get_body(preferencelist=("html",)).get_content()
    return list(dict.fromkeys(re.findall(r'href="([^"]+)"', html))), html
def reset(): shutil.rmtree(pw.OUT, ignore_errors=True); shutil.rmtree(pw.ROOT/"previous", ignore_errors=True)

# 1. real (non-dry) new draft with both caption options
reset(); os.environ["MODE"]="new"; os.environ["REGENS_USED"]="0"
pw.cmd_generate(ns(False))
msg, host, port, user = sent[-1]
d = json.loads((pw.OUT/"draft.json").read_text())
assert [c["number"] for c in d["candidates"]] == [1,2], d["candidates"]
assert [c["label"] for c in d["candidates"]] == ["Instagram Caption - Choice 1", "Instagram Caption - Choice 2"]
assert not any("http" in c["caption"] or "lovable.app" in c["caption"] for c in d["candidates"]), "no URLs in captions"
assert all("link in" in c["caption"] for c in d["candidates"])
assert not d["dry_run"] and d["regens_used"]==0 and d["mode"]=="new"
assert msg["To"]=="approver@example.com" and "sender@example.com" in msg["From"] and "[TEST]" not in msg["Subject"]
assert (host,port,user)==("smtp.example","smtp.example"[:0] or port,"sender@example.com")
hrefs, html = links(msg)
assert len(hrefs)==4+3+1, hrefs   # 2 captions x 2 images + 3 regen + skip
assert all(h.startswith("https://fdc-approvals.example.workers.dev/a/") for h in hrefs)
assert "cid:postimage-a" in html and "cid:postimage-b" in html and "Regenerations left for this draft: 3 of 3" in html
assert "Instagram Caption - Choice 1" in html and "Instagram Caption - Choice 2" in html and "Option " not in html
# the image is really attached inline
cts = [p.get_content_type() for p in msg.walk()]
assert cts.count("image/jpeg")==2 and "image/jpeg" in cts and "text/plain" in cts and "text/html" in cts, cts
assert msg.get_body(preferencelist=("plain",)).get_content().count("Approve Instagram Caption - Choice")==4
print("1 ok: new draft, 2 caption choices without URLs, email has 8 links + two inline images")
real_draft_dir = Path("saved_real"); shutil.rmtree(real_draft_dir, ignore_errors=True); shutil.copytree(pw.OUT, real_draft_dir)

# 2. regen captions: keeps image bytes, passes avoid, counts regens
def fake_fetch(run_id, dest):
    shutil.rmtree(dest, ignore_errors=True); shutil.copytree(real_draft_dir, dest); return json.loads((dest/"draft.json").read_text())
pw.fetch_draft = fake_fetch
prev_img = (real_draft_dir/"image-a.jpg").read_bytes()
reset(); os.environ.update(MODE="captions", PREV_RUN_ID="9001", REGENS_USED="1"); img_calls.clear()
pw.cmd_generate(ns(False))
d2 = json.loads((pw.OUT/"draft.json").read_text())
assert (pw.OUT/"image-a.jpg").read_bytes()==prev_img and (pw.OUT/"image-b.jpg").exists() and img_calls==[] , "image must be reused, not regenerated"
assert d2["regens_used"]==1 and d2["week"]==d["week"] and d2["theme"]==d["theme"]
assert "cf-avoid" in d2["candidates"][1]["caption"], "previous captions must be passed as 'avoid'"
_, html2 = links(sent[-1][0]); assert "2 of 3" in html2
print("2 ok: captions mode reuses image, avoids old captions, shows 2 of 3 left")

# 3. regen image: keeps captions, new image with variation
reset(); os.environ.update(MODE="image", REGENS_USED="2"); img_calls.clear()
pw.cmd_generate(ns(False))
d3 = json.loads((pw.OUT/"draft.json").read_text())
assert d3["candidates"]==d["candidates"] and len(img_calls)==2 and all(v for v,_ in img_calls) and True
_, html3 = links(sent[-1][0]); assert "1 of 3" in html3
print("3 ok: image mode keeps captions, asks for a varied image")

# 4. no regenerations left -> no regen buttons
reset(); os.environ.update(MODE="all", REGENS_USED="3")
pw.cmd_generate(ns(False))
hrefs4, html4 = links(sent[-1][0]); assert len(hrefs4)==4+1 and "No regenerations left" in html4
print("4 ok: at the cap the email offers only approve/skip")

# 5. dry run -> [TEST] email, x=1 in links, template only
reset(); os.environ.update(MODE="new", REGENS_USED="0")
pw.cmd_generate(ns(True))
m5 = sent[-1][0]; assert m5["Subject"].startswith("[TEST]")
tok = links(m5)[0][0].rsplit("/",1)[1]; body = tok.split(".")[0]
import base64; p = json.loads(base64.urlsafe_b64decode(body+"="*(-len(body)%4)))
assert p["x"]==1 and p["a"]=="approve" and p["d"]=="9001", p
d5 = json.loads((pw.OUT/"draft.json").read_text()); assert d5["dry_run"] and len(d5["candidates"])==1
print("5 ok: dry run emails a [TEST] draft whose links carry x=1")
shutil.copytree(pw.OUT, "saved_dry", dirs_exist_ok=True)

# 6. missing email settings: dry run skips, real run stops
saved = {k: os.environ.pop(k) for k in ("APPROVAL_BASE_URL","LINK_SECRET")}
n = len(sent); reset(); pw.cmd_generate(ns(True)); assert len(sent)==n
try: pw.cmd_generate(ns(False)); raise SystemExit("should have stopped")
except SystemExit as e: assert "missing settings" in str(e), e
os.environ.update(saved); print("6 ok: missing email config -> dry run continues, real run stops")

# 7. publish
posts=[]
pw.host_image = lambda path: "https://raw.example/img.jpg"
pw.publish_to_instagram = lambda url, cap, alt: posts.append((url, cap, alt)) or "M1"
archived = []; pw.archive_image = lambda path: archived.append(path.name)
os.environ["IG_ACCESS_TOKEN"]="tok"
def run_publish(draft_dir, option, dry=False):
    pw.fetch_draft = lambda rid, dest: (shutil.rmtree(dest, ignore_errors=True), shutil.copytree(draft_dir, dest), json.loads((dest/"draft.json").read_text()))[2]
    os.environ["DRAFT_RUN_ID"]="9001"; os.environ["CHOSEN_OPTION"]=str(option)
    pw.cmd_publish(argparse.Namespace(dry_run=dry, out=None))
reset(); run_publish(real_draft_dir, 2)
assert len(posts)==1 and posts[0][1]==d["candidates"][1]["caption"] and posts[0][2]=="alt cf"
assert archived == [d["images"][0]["file"]], archived
print("7 ok: approving choice 2 posts exactly the AI caption, then keeps an archive copy")
for label, fn in [("option not in draft", lambda: run_publish(real_draft_dir, 7)),
                  ("test draft", lambda: run_publish(Path("saved_dry"), 1)),
                  ("dry-run flag", lambda: run_publish(real_draft_dir, 1, dry=True))]:
    before=len(posts)
    try: fn()
    except SystemExit: pass
    assert len(posts)==before, label
print("8 ok: bad option / test draft / --dry-run never post")
exp = Path("saved_expired"); shutil.rmtree(exp, ignore_errors=True); shutil.copytree(real_draft_dir, exp)
dj = json.loads((exp/"draft.json").read_text()); dj["expires_at"]=int(time.time())-5; (exp/"draft.json").write_text(json.dumps(dj))
before=len(posts)
try: run_publish(exp, 1)
except SystemExit as e: assert "expired" in str(e)
assert len(posts)==before; print("9 ok: expired draft refuses to post")
h = approval_email._caption_html("For life's occasions & more. #Smile #FDC\nNext line")
assert "life&#x27;s" in h and "#x27</span>" not in h and "&amp; more" in h, h
assert h.count('<span style="color:#00376b;">#') == 2 and "<br>Next line" in h, h
print("10 ok: apostrophes and & show correctly in the email; hashtags still highlighted")

c = pw.strip_urls("Hook.\n\nBook via the link in our bio.\nhttps://fdc-invisalign-cosmetic.lovable.app/\n\nVisit www.example.com today. Follow Fdc.dental")
assert "http" not in c and "www." not in c and "lovable" not in c and "Follow Fdc.dental" in c and "\n\n\n" not in c, c
cfg = pw.load_config(); th = {"topic": "T", "angle": "A"}
assert "lovable.app" not in pw.fallback_caption(cfg, th)["caption"] and "link in our bio" in pw.fallback_caption(cfg, th)["caption"]
_, prompt = pw._prompts(cfg, th)
assert "300-600 characters" in prompt and "lovable.app" not in prompt and "Do NOT include any website" in prompt, prompt
print("11 ok: URLs are removed from captions, the account name stays")

# ---- 12. Cloudflare "daily allowance used up": wait 30, 45, 60 minutes, then give up and email the approver
waits = []; pw.time.sleep = lambda s: waits.append(s)
tries = {"n": 0}
def limited_img(fail_rounds):
    def f(cfg, theme, path, dry, variation="", person=None, label="", letter="A"):
        tries["n"] += 1
        if (tries["n"] + 1) // 2 <= fail_rounds:  # two images per round
            raise pw.CloudflareLimitError("Cloudflare refused: daily free allowance used up")
        pw.make_placeholder(theme, path, label); return "prompt"
    return f
reset(); os.environ["MODE"] = "new"; os.environ["REGENS_USED"] = "0"; tries["n"] = 0; n_sent = len(sent)
pw.generate_image = limited_img(2)
cf_calls = []
real_cf = pw.cloudflare_caption
pw.cloudflare_caption = lambda cfg, theme, avoid=None: (cf_calls.append(1), None if len(cf_calls) == 1 else good("cf"))[1]
pw.cmd_generate(ns(False))
pw.cloudflare_caption = real_cf
assert waits == [30 * 60, 45 * 60], waits
d = json.loads((pw.OUT/"draft.json").read_text())
assert len(cf_calls) == 2 and [c["number"] for c in d["candidates"]] == [1, 2], "AI caption retried after the wait"
assert len(d["images"]) == 2 and len(sent) == n_sent + 1 and "Approve" in sent[-1][0]["Subject"]
print("12 ok: refused twice, then made after waiting 30 and 45 minutes; normal approval email sent")

reset(); waits.clear(); tries["n"] = 0; n_sent = len(sent)
pw.generate_image = limited_img(99)
os.environ.update(CLOUDFLARE_ACCOUNT_ID="acct-secret-42", GITHUB_REPOSITORY="owner/repo", GITHUB_RUN_ID="777")
sys.argv = ["post_weekly.py", "generate"]
try: pw.main(); raise AssertionError("should exit")
except SystemExit as e: assert "after waiting 30, 45, 60 minutes" in str(e.code), e.code
assert waits == [30 * 60, 45 * 60, 60 * 60], waits
assert tries["n"] == 8, tries  # first try + 3 retries, two images each
fail = sent[-1][0]; body = fail.get_content()
assert len(sent) == n_sent + 1 and fail["Subject"] == "Today's Instagram draft could not be made", fail["Subject"]
assert fail["To"] == "approver@example.com" and "Nothing has been posted" in body and "Run workflow" in body
assert "https://github.com/owner/repo/actions/runs/777" in body and "daily free allowance" in body
print("13 ok: still refused after 30, 45 and 60 minutes; the run fails and the approver gets a failure email")

reset(); n_sent = len(sent)
sys.argv = ["post_weekly.py", "generate", "--dry-run"]
pw.generate_image = lambda *a, **k: (_ for _ in ()).throw(SystemExit("boom acct-secret-42"))
try: pw.main()
except SystemExit: pass
assert len(sent) == n_sent, "no failure email for a dry run"
pw.notify_failure("boom acct-secret-42")
assert "acct-secret-42" not in sent[-1][0].get_content() and "boom ***" in sent[-1][0].get_content()
os.environ["MODE"] = "image"; pw.notify_failure("x")
assert sent[-1][0]["Subject"] == "The new version you asked for could not be made"
os.environ["MODE"] = "new"
print("14 ok: no failure email for test runs; secrets are blanked out; regenerations get their own wording")

# ---- 15. IMAGE_HOST=cloudflare: signed upload to the Worker, then a private archive copy in images/
assert pw.upload_signature("test-secret-123", "1700000000", b"\xff\xd8\xff\xe0fdc-test") == \
    "cead4ac3ed178c24ab710efd5357be44b6170dc9a6230530e348bb2ebb475643", "same test vector as worker.test.mjs"
class Resp:
    def __init__(self, status, data=None, text=""): self.status_code, self._d, self.text = status, data, text
    def json(self): return self._d
img = Path("upload_me.jpg"); img.write_bytes(b"\xff\xd8\xff\xe0fdc-test")
http = {"post": [], "head": [], "put": []}
def fake_post(url, data=None, headers=None, timeout=None):
    http["post"].append((url, data, headers))
    return Resp(200, {"url": "https://fdc-approvals.example.workers.dev/img/" + "a" * 32 + ".jpg"})
pw.requests.post = fake_post
pw.requests.head = lambda url, timeout=None: (http["head"].append(url), Resp(200))[1]
os.environ["IMAGE_HOST"] = "cloudflare"
url = real_host_image(img)
assert url == "https://fdc-approvals.example.workers.dev/img/" + "a" * 32 + ".jpg" and http["head"] == [url]
purl, pdata, ph = http["post"][0]
assert purl == "https://fdc-approvals.example.workers.dev/img" and pdata == img.read_bytes() and ph["Content-Type"] == "image/jpeg"
assert ph["X-FDC-Signature"] == pw.upload_signature("test-secret-123", ph["X-FDC-Time"], pdata)
assert "test-secret-123" not in json.dumps(ph), "the secret itself is never sent"
pw.requests.post = lambda *a, **k: Resp(403, text='{"error":"bad signature"}')
try: real_host_image(img); raise AssertionError("should stop")
except SystemExit as e: assert "did not accept the image: 403" in str(e), e
pw.requests.post = lambda *a, **k: Resp(200, {"url": "https://evil.example/x.jpg"})
try: real_host_image(img); raise AssertionError("should stop")
except SystemExit as e: assert "unexpected image address" in str(e), e
saved = os.environ.pop("LINK_SECRET")
try: real_host_image(img); raise AssertionError("should stop")
except SystemExit as e: assert "LINK_SECRET" in str(e)
os.environ["LINK_SECRET"] = saved
# archive copy: commits to images/ for cloudflare, never stops the job, not done twice for github
commits = []; real_commit = pw._commit_to_repo
pw._commit_to_repo = lambda path: commits.append(path.name) or "https://raw.example/x"
real_archive(img); assert commits == ["upload_me.jpg"], commits
pw._commit_to_repo = lambda path: (_ for _ in ()).throw(SystemExit("Could not save image to GitHub: 403"))
real_archive(img)   # only a warning
os.environ["IMAGE_HOST"] = "github"; commits.clear(); pw._commit_to_repo = lambda path: commits.append(1) or "u"
real_archive(img); assert commits == [], "github host already commits the image"
pw._commit_to_repo = real_commit
print("15 ok: cloudflare host signs the upload, checks the answer, and the archive copy never blocks a live post")
