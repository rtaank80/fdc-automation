// FDC approval Worker.
//
// Turns the buttons in the weekly approval email into GitHub Actions runs.
//   GET  /a/<token>  shows a confirmation page (changes nothing, so mail scanners that open links are harmless)
//   POST /c/<token>  the Confirm button: checks the link, then starts the right GitHub workflow
//   POST /img        the publish job uploads the approved image (signed, see below); answers {"url": ...}
//   GET  /img/<id>.jpg  serves an uploaded image so Instagram can fetch it. It is deleted after IMAGE_HOURS.
//
// Each link is signed in post_weekly.py / approval_email.py:
//   token = base64url(JSON payload) + "." + base64url(HMAC-SHA256(LINK_SECRET, base64url(JSON payload)))
//   payload: a=action, d=draft run id, c=option number, l=option label, w=ISO week, e=expiry (epoch s),
//            i=chosen image letter (A or B), t=post topic, x=1 for a test (dry run) draft
//   w is the draft chain id: one scheduled run plus any regenerations of it. There is no weekly limit;
//   how often posts happen is controlled by the schedule in weekly.yml.
//
// Settings (Worker > Settings > Variables and secrets, Bindings):
//   LINK_SECRET  (secret)  same value as the LINK_SECRET secret in GitHub
//   GH_TOKEN     (secret)  fine-grained GitHub token for this repo only, permission "Actions: Read and write"
//   GH_REPO      (text)    owner/repo, e.g. rtinnovations/fdc-instagram
//   GH_REF       (text, optional) branch holding the workflows, default "main"
//   MAX_REGENS   (text, optional) regenerations allowed per draft, default 3
//   STATE        (KV namespace binding) remembers used links, each draft's decision and its regeneration count
//   DAILY_DRAFT  (text, optional) what the daily schedule does: "live" (default) a real draft, "test" a TEST
//                draft that cannot post, "off" nothing
//   DRAFT_UK_HOUR (text, optional) UK hour (0-23) at which the daily draft starts, default 10
//   IMAGE_HOURS  (text, optional) hours an uploaded post image is kept before Cloudflare deletes it, default 48
//
// Image upload (IMAGE_HOST=cloudflare in publish.yml): the body is the JPEG itself, with headers
//   X-FDC-Time       upload time (epoch seconds); must be within 5 minutes of the Worker's clock
//   X-FDC-Signature  hex HMAC-SHA256(LINK_SECRET, "img-upload." + time + "." + hex SHA-256 of the body)
// Each signature works once. The signed text contains dots, which a link token's signed part (base64url) never
// does, so an upload signature can never be used as an email link or the other way round. The image is stored
// in KV under a random name with an expiry, so Cloudflare deletes it by itself; the GitHub publish job keeps a
// private archive copy in the repo's images/ folder.

// Daily schedule (Cloudflare Cron Trigger, set by deploy-worker.yml): it fires at :00 and :30 of 09:00 and
// 10:00 UTC. Only the fires in the chosen UK hour act, so the draft starts at 10:00 UK time in both GMT and BST;
// the :30 fire is a retry for when GitHub refused the first one. KV key "daily:<UK date>" stops a second draft
// on the same day. The draft it starts is a normal one: nothing is posted until the approver clicks Approve.

const DAY = 86400;
const MAX_IMAGE_BYTES = 8 * 1024 * 1024;   // Instagram's own limit for a JPEG
const UPLOAD_WINDOW = 300;                 // seconds an upload signature stays valid

const ACTIONS = {
  approve: { label: "Approve and post to Instagram" },
  captions: { label: "Generate new captions (keeping the images)", mode: "captions" },
  image: { label: "Generate new images (keeping the captions)", mode: "image" },
  all: { label: "Generate new images and new captions", mode: "all" },
  skip: { label: "Skip this draft (post nothing)" },
};

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    if (url.pathname === "/") return page("FDC approvals", "The approval service is running.", 200);
    if (url.pathname === "/img") return uploadImage(request, env, url);
    const im = url.pathname.match(/^\/img\/([0-9a-f]{32})\.jpg$/);
    if (im) return serveImage(request, env, im[1]);

    const m = url.pathname.match(/^\/(a|c)\/([A-Za-z0-9_.\-]+)$/);
    if (!m) return page("Not found", "This link is not valid.", 404);
    const [, kind, token] = m;

    const p = await verify(token, env.LINK_SECRET);
    if (!p || !ACTIONS[p.a]) {
      return page("Link not valid", "This link is invalid or has expired. If this draft is still undecided, use the buttons in the newest email, or wait for the next scheduled draft.", 400);
    }

    if (kind === "a") {
      if (request.method !== "GET" && request.method !== "HEAD") return new Response("Method not allowed", { status: 405 });
      return confirmPage(p, token);
    }
    if (request.method !== "POST") return new Response("Method not allowed", { status: 405 });
    return doAction(p, token, env);
  },

  // Cloudflare Cron Trigger. A thrown error marks the fire as failed in the Worker's logs.
  async scheduled(controller, env) {
    const result = await dailyDraft(new Date(controller.scheduledTime || Date.now()), env);
    console.log(`daily draft: ${result}`);
  },
};

// The UK date (YYYY-MM-DD) and hour for a moment in time, following GMT/BST.
function ukClock(when) {
  const parts = Object.fromEntries(
    new Intl.DateTimeFormat("en-GB", {
      timeZone: "Europe/London", year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", hourCycle: "h23",
    }).formatToParts(when).map((x) => [x.type, x.value]),
  );
  return { date: `${parts.year}-${parts.month}-${parts.day}`, hour: parseInt(parts.hour, 10) };
}

// Starts the day's draft (weekly.yml, mode new) once per UK day. Returns a short description of what it did.
async function dailyDraft(when, env) {
  const setting = String(env.DAILY_DRAFT || "live").trim().toLowerCase();
  if (setting === "off") return "off (DAILY_DRAFT=off)";
  const test = setting === "test";
  const hour = parseInt(env.DRAFT_UK_HOUR || "10", 10);
  const uk = ukClock(when);
  if (uk.hour !== hour) return `not the draft hour (UK ${uk.hour}:00, draft hour ${hour}:00)`;
  const key = `daily:${uk.date}`;
  if (await env.STATE.get(key)) return `already started for ${uk.date}`;
  await dispatch(env, "weekly.yml", {
    mode: "new",
    dry_run: test ? "true" : "false",
    refresh_token: test ? "false" : "true",   // the Instagram token refresh used to run with the GitHub schedule
    source: test ? "cloudflare-test" : "cloudflare",
  });
  await env.STATE.put(key, test ? "test" : "live", { expirationTtl: 3 * DAY });
  return `started ${test ? "a TEST" : "a live"} draft for ${uk.date}`;
}

async function doAction(p, token, env) {
  const kv = env.STATE;
  const chain = String(p.w);
  const usedKey = await tokenKey(token);
  const test = p.x === 1;
  // Test (dry run) drafts keep their own state, separate from real drafts.
  const scope = test ? "test" : "live";
  const decidedKey = `decided:${chain}:${scope}`;
  const regenKey = `regen:${chain}:${scope}`;
  const maxRegens = parseInt(env.MAX_REGENS || "3", 10);
  const ttl = { expirationTtl: 14 * DAY };

  if (await kv.get(usedKey)) return page("Already done", "This link has already been used.", 200);
  // A real draft (with its regenerations) can be approved or skipped once, so two Approve buttons in one email
  // cannot post twice. There is no limit per week. Test drafts have no lock and can be clicked repeatedly.
  const decided = test ? null : await kv.get(decidedKey);
  if (decided) return page("Already decided", `This draft has already been decided (${decided}). Nothing more will happen.`, 200);

  try {
    if (p.a === "skip") {
      if (!test) await kv.put(decidedKey, "skipped", ttl);
      await kv.put(usedKey, "1", ttl);
      return page("Skipped", "Nothing will be posted for this draft. The next draft arrives with the next scheduled run.", 200);
    }

    if (p.a === "approve") {
      await dispatch(env, "publish.yml", {
        draft_run_id: String(p.d),
        option: String(p.c),
        image: /^[A-Z]$/.test(String(p.i || "")) ? String(p.i) : "A",
        dry_run: test ? "true" : "false",
      });
      if (!test) await kv.put(decidedKey, `option ${p.c} with image ${p.i || "A"} approved`, ttl);
      await kv.put(usedKey, "1", ttl);
      return page(
        test ? "Test approved" : "Approved",
        test
          ? `Option ${esc(p.c)} with image ${esc(p.i || "A")} approved. This was a test draft, so nothing is posted to Instagram; check the "Publish approved post" run in GitHub Actions.`
          : `Option ${esc(p.c)} with image ${esc(p.i || "A")} approved. It is being posted to Instagram now and should appear within a few minutes.`,
        200,
      );
    }

    // Regeneration: captions, image or all
    const used = parseInt((await kv.get(regenKey)) || "0", 10);
    if (!test && used >= maxRegens) {
      return page("Limit reached", `You have already used all ${maxRegens} regenerations for this draft. Approve one of the options you have, or skip it.`, 200);
    }
    await dispatch(env, "weekly.yml", {
      mode: ACTIONS[p.a].mode,
      prev_run_id: String(p.d),
      regens_used: String(used + 1),
      dry_run: test ? "true" : "false",
    });
    await kv.put(regenKey, String(used + 1), ttl);
    await kv.put(usedKey, "1", ttl);
    const left = maxRegens - (used + 1);
    return page("On its way", `A new draft is being made and will arrive by email in a few minutes. Regenerations left for this draft: ${left} of ${maxRegens}.`, 200);
  } catch (err) {
    // Nothing is marked as used, so the button can be tried again.
    return page("Something went wrong", `GitHub did not accept the request (${esc(String(err.message).slice(0, 160))}). You can try the button again in a minute.`, 502);
  }
}

async function dispatch(env, workflowFile, inputs) {
  const r = await fetch(`https://api.github.com/repos/${env.GH_REPO}/actions/workflows/${workflowFile}/dispatches`, {
    method: "POST",
    headers: {
      Authorization: `Bearer ${env.GH_TOKEN}`,
      Accept: "application/vnd.github+json",
      "X-GitHub-Api-Version": "2022-11-28",
      "User-Agent": "fdc-approvals-worker",
      "Content-Type": "application/json",
    },
    body: JSON.stringify({ ref: env.GH_REF || "main", inputs }),
  });
  if (r.status !== 204) throw new Error(`GitHub ${r.status}: ${(await r.text()).slice(0, 120)}`);
}

// ---------------------------------------------------------------- post images
const hex = (buf) => [...new Uint8Array(buf)].map((b) => b.toString(16).padStart(2, "0")).join("");

function imageHours(env) {
  const h = parseInt(env.IMAGE_HOURS || "48", 10);
  return Number.isFinite(h) && h >= 1 ? Math.min(h, 24 * 30) : 48;
}

async function uploadImage(request, env, url) {
  const json = (obj, status) => new Response(JSON.stringify(obj), {
    status, headers: { "content-type": "application/json", "cache-control": "no-store" },
  });
  if (request.method !== "POST") return json({ error: "method not allowed" }, 405);
  if (!env.LINK_SECRET) return json({ error: "not configured" }, 403);
  const ts = String(request.headers.get("x-fdc-time") || "");
  const sig = String(request.headers.get("x-fdc-signature") || "").toLowerCase();
  if (!/^\d{9,11}$/.test(ts) || !/^[0-9a-f]{64}$/.test(sig)) return json({ error: "not signed" }, 403);
  if (Math.abs(Date.now() / 1000 - Number(ts)) > UPLOAD_WINDOW) return json({ error: "signature expired" }, 403);
  const declared = parseInt(request.headers.get("content-length") || "0", 10);
  if (declared > MAX_IMAGE_BYTES) return json({ error: "too large" }, 413);
  const body = await request.arrayBuffer();
  if (body.byteLength > MAX_IMAGE_BYTES) return json({ error: "too large" }, 413);
  const enc = new TextEncoder();
  const digest = hex(await crypto.subtle.digest("SHA-256", body));
  const key = await crypto.subtle.importKey("raw", enc.encode(env.LINK_SECRET), { name: "HMAC", hash: "SHA-256" }, false, ["verify"]);
  const sigBytes = Uint8Array.from(sig.match(/../g), (h) => parseInt(h, 16));
  // crypto.subtle.verify compares in constant time
  if (!(await crypto.subtle.verify("HMAC", key, sigBytes, enc.encode(`img-upload.${ts}.${digest}`)))) {
    return json({ error: "bad signature" }, 403);
  }
  const bytes = new Uint8Array(body);
  if (bytes.length < 4 || bytes[0] !== 0xff || bytes[1] !== 0xd8 || bytes[2] !== 0xff) return json({ error: "not a JPEG" }, 415);
  const usedKey = `upsig:${sig}`;
  if (await env.STATE.get(usedKey)) return json({ error: "signature already used" }, 403);
  await env.STATE.put(usedKey, "1", { expirationTtl: 2 * UPLOAD_WINDOW });
  const id = hex(crypto.getRandomValues(new Uint8Array(16)));
  const hours = imageHours(env);
  await env.STATE.put(`img:${id}`, body, { expirationTtl: hours * 3600 });
  return json({ url: `${url.origin}/img/${id}.jpg`, expires_in_hours: hours }, 200);
}

async function serveImage(request, env, id) {
  if (request.method !== "GET" && request.method !== "HEAD") return new Response("Method not allowed", { status: 405 });
  const body = await env.STATE.get(`img:${id}`, "arrayBuffer");
  if (!body) return new Response("Not found", { status: 404, headers: { "cache-control": "no-store" } });
  return new Response(request.method === "HEAD" ? null : body, {
    status: 200,
    headers: {
      "content-type": "image/jpeg",
      "content-length": String(body.byteLength),
      "cache-control": "public, max-age=600",
      "x-content-type-options": "nosniff",
      "x-robots-tag": "noindex",
    },
  });
}

// ---------------------------------------------------------------- token checking
function b64uToBytes(s) {
  s = s.replace(/-/g, "+").replace(/_/g, "/");
  while (s.length % 4) s += "=";
  const bin = atob(s);
  return Uint8Array.from(bin, (c) => c.charCodeAt(0));
}

async function verify(token, secret) {
  if (!secret) return null;
  const dot = token.indexOf(".");
  if (dot < 1) return null;
  const body = token.slice(0, dot);
  const sig = token.slice(dot + 1);
  try {
    const enc = new TextEncoder();
    const key = await crypto.subtle.importKey("raw", enc.encode(secret), { name: "HMAC", hash: "SHA-256" }, false, ["verify"]);
    // crypto.subtle.verify compares in constant time
    if (!(await crypto.subtle.verify("HMAC", key, b64uToBytes(sig), enc.encode(body)))) return null;
    const p = JSON.parse(new TextDecoder().decode(b64uToBytes(body)));
    if (!p || typeof p.e !== "number" || p.e * 1000 < Date.now()) return null;
    return p;
  } catch {
    return null;
  }
}

async function tokenKey(token) {
  const d = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(token));
  return "used:" + [...new Uint8Array(d)].map((b) => b.toString(16).padStart(2, "0")).join("");
}

// ---------------------------------------------------------------- pages
function esc(s) {
  return String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

function confirmPage(p, token) {
  const act = ACTIONS[p.a];
  const detail = p.a === "approve" ? `${p.l ? esc(p.l) : `Option ${esc(p.c)}`} with image ${esc(p.i || "A")}` : "";
  const test = p.x === 1 ? `<p class="test">Test draft: nothing will be posted to Instagram.</p>` : "";
  const body = `
    <p class="topic">${esc(p.t || "")}</p>
    <h1>${esc(act.label)}</h1>
    ${detail ? `<p class="detail">${detail}</p>` : ""}
    ${test}
    <form method="POST" action="/c/${esc(token)}">
      <button type="submit">Confirm</button>
    </form>
    <p class="small">Nothing happens until you press Confirm. You can close this page to cancel.</p>`;
  return html("Confirm", body, 200);
}

function page(title, message, status) {
  return html(title, `<h1>${esc(title)}</h1><p>${message}</p>`, status);
}

function html(title, body, status) {
  const doc = `<!doctype html><html lang="en-GB"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><meta name="robots" content="noindex">
<title>${esc(title)} | FDC approvals</title>
<style>
  :root{color-scheme:light dark}
  body{font:17px/1.55 Georgia,serif;max-width:34rem;margin:12vh auto;padding:0 1rem;color:#23302f;background:#f3f5f4}
  @media (prefers-color-scheme:dark){body{color:#e6eceb;background:#141a1a}}
  h1{font-size:1.5rem;line-height:1.3;margin:.3rem 0}
  .topic{font:700 .8rem Arial,sans-serif;letter-spacing:.08em;text-transform:uppercase;color:#1f7a78;margin:0}
  .detail{font-weight:700}.test{background:#fff4d6;color:#6b4e00;padding:.6rem .8rem;border-radius:8px;font:600 .9rem Arial,sans-serif}
  .small{font-size:.85rem;opacity:.75}
  button{font:600 1rem Arial,sans-serif;background:#1f7a78;color:#fff;border:0;border-radius:8px;padding:.85rem 1.8rem;cursor:pointer;margin:.8rem 0}
</style></head><body>${body}</body></html>`;
  return new Response(doc, {
    status,
    headers: {
      "content-type": "text/html; charset=utf-8",
      "cache-control": "no-store",
      "referrer-policy": "no-referrer",
      "x-robots-tag": "noindex",
    },
  });
}
