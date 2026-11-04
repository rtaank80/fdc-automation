// Tests for worker/worker.js with GitHub and KV replaced by stubs. Run: node tests/worker.test.mjs
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import crypto from "node:crypto";
import { fileURLToPath } from "node:url";

const here = path.dirname(fileURLToPath(import.meta.url));
const tmp = path.join(fs.mkdtempSync(path.join(os.tmpdir(), "fdcw-")), "worker.mjs");
fs.copyFileSync(path.join(here, "..", "worker", "worker.js"), tmp);   // .mjs copy so Node treats it as an ES module
const worker = (await import(tmp)).default;

const SECRET = "test-secret-123";
const NOW = Math.floor(Date.now() / 1000);
const b64u = (b) => Buffer.from(b).toString("base64url");
function sign(p, secret = SECRET) {
  const body = b64u(JSON.stringify(p));
  return body + "." + crypto.createHmac("sha256", secret).update(body).digest("base64url");
}
const base = { d: "9001", w: "2026-W44", e: NOW + 3600, t: "Whitening", x: 0 };
const mk = (o) => sign({ ...base, ...o });
const T = {
  approve2: mk({ a: "approve", c: 2, l: "Instagram Caption - Choice 2" }), approve3: mk({ a: "approve", c: 3, l: "Cloudflare" }),
  approve2B: mk({ a: "approve", c: 2, i: "B", l: "Claude" }), approveBad: mk({ a: "approve", c: 2, i: "<x>", l: "Claude" }),
  captions: mk({ a: "captions" }), image: mk({ a: "image" }), all: mk({ a: "all" }), skip: mk({ a: "skip" }),
  captions_b: mk({ d: "9002", a: "captions" }),
  expired: mk({ e: NOW - 3600, a: "approve", c: 2, l: "x" }), wrongkey: sign({ ...base, a: "approve", c: 2 }, "other-secret"),
  dry_approve: mk({ d: "9003", x: 1, a: "approve", c: 1, l: "Default template" }),
  xss: mk({ d: "9004", t: "<script>alert(1)</script>", a: "approve", c: 1, l: "<img src=x onerror=alert(1)>" }),
  otherweek: mk({ d: "9005", w: "2026-W45", a: "approve", c: 2, l: "Claude" }),
  unicode: mk({ d: "9006", t: "Smile \u2013 caf\u00e9 \u2764", a: "approve", c: 2, l: "Claude" }),
  same_test_approve: mk({ d: "9101", x: 1, a: "approve", c: 1, l: "Default template" }),
  same_live_approve: mk({ d: "9101", a: "approve", c: 2, l: "Claude" }),
  same_test_regen: mk({ d: "9102", x: 1, a: "captions" }), same_live_regen: mk({ d: "9103", a: "captions" }),
  t_approve_a: mk({ d: "9200", w: "2026-W43", t: "Test week", x: 1, a: "approve", c: 1, l: "Default template" }),
  t_approve_b: mk({ d: "9201", w: "2026-W43", t: "Test week", x: 1, a: "approve", c: 1, l: "Default template" }),
  t_skip: mk({ d: "9202", w: "2026-W43", t: "Test week", x: 1, a: "skip" }),
};
for (let i = 0; i < 5; i++) T[`t_regen${i}`] = mk({ d: String(9210 + i), w: "2026-W43", t: "Test week", x: 1, a: "captions" });
let fails = 0; const ok = (c, m) => { if (!c) { fails++; console.log("FAIL:", m); } else console.log("ok  :", m); };

function makeEnv() {
  const m = new Map();
  return { LINK_SECRET: "test-secret-123", GH_TOKEN: "ghp_secret", GH_REPO: "me/repo", MAX_REGENS: "3",
    STATE: { get: async k => m.get(k) ?? null, put: async (k, v) => { m.set(k, v); }, _m: m } };
}
let calls = [], ghStatus = 204;
globalThis.fetch = async (url, init) => { calls.push({ url, init, body: JSON.parse(init.body) }); return new Response(ghStatus === 204 ? null : "boom", { status: ghStatus }); };
const get = (env, t) => worker.fetch(new Request(`https://w.example/a/${t}`), env);
const post = (env, t) => worker.fetch(new Request(`https://w.example/c/${t}`, { method: "POST" }), env);
const text = async r => (await r).text();

// --- GET shows a confirm page and changes nothing
let env = makeEnv(); calls = [];
let r = await get(env, T.approve2); let html = await r.text();
ok(r.status === 200 && html.includes("Confirm") && html.includes("Instagram Caption - Choice 2 with image A") && html.includes(`action="/c/${T.approve2}"`), "GET approve -> confirm page with option and POST form");
ok(calls.length === 0 && env.STATE._m.size === 0, "GET changes nothing (scanner-safe)");
ok(r.headers.get("cache-control") === "no-store" && r.headers.get("referrer-policy") === "no-referrer", "no-store + no-referrer headers");

// --- bad links
for (const k of ["expired", "wrongkey"]) { r = await get(env, T[k]); ok(r.status === 400, `${k} link rejected`); }
const t = T.approve2; const flip = t.slice(0, -2) + (t.at(-2) === "A" ? "B" : "A") + t.at(-1);
r = await get(env, flip); ok(r.status === 400, "tampered signature rejected");
const [b, s] = t.split("."); const pl = JSON.parse(Buffer.from(b, "base64url")); pl.c = 3;
r = await get(env, Buffer.from(JSON.stringify(pl)).toString("base64url") + "." + s); ok(r.status === 400, "tampered payload (option 2 -> 3) rejected");
r = await post(env, flip); ok(r.status === 400 && calls.length === 0, "POST with bad token does nothing");
r = await worker.fetch(new Request(`https://w.example/c/${t}`), env); ok(r.status === 405, "GET on /c/ is 405");
r = await worker.fetch(new Request(`https://w.example/a/${t}`, { method: "POST" }), env); ok(r.status === 405, "POST on /a/ is 405");
r = await worker.fetch(new Request("https://w.example/nope"), env); ok(r.status === 404, "unknown path 404");
ok((await worker.fetch(new Request(`https://w.example/a/${t}`), { ...env, LINK_SECRET: "" })).status === 400, "no secret configured -> rejects everything");

// --- escaping
html = await text(get(env, T.xss));
ok(!html.includes("<script>alert") && html.includes("&lt;script&gt;") && !html.includes("<img src=x"), "HTML in topic/label is escaped");
html = await text(get(env, T.unicode)); ok(html.includes("Smile") && html.includes("café"), "unicode topic survives (cross-language UTF-8)");

// --- approve
env = makeEnv(); calls = [];
r = await post(env, T.approve2); html = await r.text();
ok(r.status === 200 && html.includes("Approved"), "POST approve -> success page");
ok(calls.length === 1 && calls[0].url === "https://api.github.com/repos/me/repo/actions/workflows/publish.yml/dispatches", "dispatches publish.yml on the right repo");
ok(JSON.stringify(calls[0].body) === JSON.stringify({ ref: "main", inputs: { draft_run_id: "9001", option: "2", image: "A", dry_run: "false" } }), "inputs: run 9001, option 2, image A (the default), dry_run false");
ok(calls[0].init.headers.Authorization === "Bearer ghp_secret" && calls[0].init.headers["User-Agent"], "sends token and a User-Agent");
ok(!html.includes("ghp_secret"), "token never shown");
r = await post(env, T.approve2); ok((await r.text()).includes("already been used") && calls.length === 1, "same link twice -> no second post");
r = await post(env, T.approve3); ok((await r.text()).includes("already been decided") && calls.length === 1, "other option after approval -> blocked");
r = await post(env, T.captions); ok((await r.text()).includes("already been decided") && calls.length === 1, "regenerate after approval -> blocked");
r = await post(env, T.otherweek); ok(r.status === 200 && calls.length === 2, "a different week is independent");

// --- test (dry-run) draft
env = makeEnv(); calls = []; await post(env, T.dry_approve);
ok(calls[0].body.inputs.dry_run === "true", "test draft approve -> dry_run true");

// --- regenerations and the weekly cap
env = makeEnv(); calls = [];
for (const [k, n] of [["captions", 1], ["image", 2], ["all", 3]]) {
  r = await post(env, T[k]); html = await r.text();
  const c = calls.at(-1);
  ok(c.url.endsWith("/weekly.yml/dispatches") && c.body.inputs.mode === k && c.body.inputs.prev_run_id === "9001" && c.body.inputs.regens_used === String(n), `regen '${k}' -> weekly.yml mode=${k}, regens_used=${n}`);
  ok(html.includes(`${3 - n} of 3`), `page says ${3 - n} of 3 left`);
}
const before = calls.length; r = await post(env, T.captions_b);
ok((await r.text()).includes("Limit reached") && calls.length === before, "4th regeneration refused, no dispatch");
r = await post(env, T.approve2); ok(r.status === 200 && calls.at(-1).url.endsWith("publish.yml/dispatches"), "approving still works after the cap");

// --- skip
env = makeEnv(); calls = [];
r = await post(env, T.skip); ok((await r.text()).includes("Skipped") && calls.length === 0, "skip dispatches nothing");
r = await post(env, T.approve2); ok((await r.text()).includes("already been decided") && calls.length === 0, "approve after skip -> blocked");

// --- GitHub failure leaves the link reusable
env = makeEnv(); calls = []; ghStatus = 422;
r = await post(env, T.approve2); html = await r.text();
ok(r.status === 502 && html.includes("try the button again") && !html.includes("ghp_secret"), "GitHub error -> friendly 502");
ok(env.STATE._m.size === 0, "nothing marked used or decided after a failure");
ghStatus = 204; r = await post(env, T.approve2); ok(r.status === 200 && calls.length === 2, "retry then succeeds");

// --- test drafts never block or use up the live week
env = makeEnv(); calls = [];
await post(env, T.same_test_approve);
r = await post(env, T.same_live_approve);
ok(r.status === 200 && (await r.text()).includes("Approved") && calls.length === 2 && calls[1].body.inputs.dry_run === "false", "a test approval does not block the real approval in the same week");
env = makeEnv(); calls = [];
await post(env, T.same_test_regen);
await post(env, T.same_live_regen);
ok(calls[1].body.inputs.regens_used === "1", "test regenerations do not use up the live allowance");

// --- test drafts: no weekly lock, no cap, but each link still works once
env = makeEnv(); calls = [];
r = await post(env, T.t_approve_a); ok(r.status === 200 && calls.length === 1 && calls[0].body.inputs.dry_run === "true", "test approve works");
r = await post(env, T.t_regen0); ok((await r.text()).includes("On its way") && calls.length === 2, "test regenerate works AFTER a test approval (the bug)");
r = await post(env, T.t_approve_b); ok(r.status === 200 && calls.length === 3, "a second test approval in the same week works");
r = await post(env, T.t_skip); ok((await r.text()).includes("Skipped"), "test skip works after test approvals");
for (let i = 1; i < 5; i++) await post(env, T[`t_regen${i}`]);
ok(calls.length === 7, "test regenerations are not capped (5 in a row all dispatched)");
r = await post(env, T.t_regen0); ok((await r.text()).includes("already been used") && calls.length === 7, "a test link still only works once");
ok(!env.STATE._m.has("decided:2026-W43:test") && ![...env.STATE._m.keys()].some(k => k.startsWith("decided:")), "no weekly lock is written for test drafts");

// --- the chosen image letter is passed on
env = makeEnv(); calls = [];
r = await get(env, T.approve2B); ok((await r.text()).includes("with image B"), "confirm page shows the chosen image");
r = await post(env, T.approve2B); ok(calls[0].body.inputs.image === "B" && (await r.text()).includes("image B"), "approve with image B dispatches image=B");
env = makeEnv(); calls = [];
await post(env, T.approveBad); ok(calls[0].body.inputs.image === "A", "an invalid image letter falls back to A");

// --- daily schedule (Cloudflare Cron Trigger)
const fire = async (env, iso) => { const logs = []; const orig = console.log; console.log = (m) => logs.push(m); try { await worker.scheduled({ scheduledTime: Date.parse(iso), cron: "0,30 9,10 * * *" }, env); } finally { console.log = orig; } return logs.join("\n"); };
env = makeEnv(); calls = [];
let log = await fire(env, "2026-11-03T09:00:00Z");   // GMT: 09:00 UTC is 09:00 UK
ok(calls.length === 0 && log.includes("not the draft hour"), "winter: 09:00 UTC fire does nothing");
log = await fire(env, "2026-11-03T10:00:00Z");
ok(calls.length === 1 && calls[0].url === "https://api.github.com/repos/me/repo/actions/workflows/weekly.yml/dispatches", "winter: 10:00 UTC (10:00 UK) dispatches weekly.yml");
ok(JSON.stringify(calls[0].body) === JSON.stringify({ ref: "main", inputs: { mode: "new", dry_run: "false", refresh_token: "true", source: "cloudflare" } }), "daily draft inputs: new, live, token refresh, source cloudflare");
ok(env.STATE._m.get("daily:2026-11-03") === "live", "the day is recorded in KV");
log = await fire(env, "2026-11-03T10:30:00Z");
ok(calls.length === 1 && log.includes("already started"), "the 10:30 retry fire does not make a second draft");
await fire(env, "2026-11-04T10:00:00Z"); ok(calls.length === 2, "the next day gets its own draft");
env = makeEnv(); calls = [];
await fire(env, "2026-07-01T10:00:00Z"); ok(calls.length === 0, "summer: 10:00 UTC is 11:00 UK, does nothing");
await fire(env, "2026-07-01T09:00:00Z"); ok(calls.length === 1, "summer: 09:00 UTC (10:00 BST) dispatches");
await fire(env, "2026-03-29T09:00:00Z"); ok(calls.length === 2, "clock-change day (BST starts) still gets a draft at 10:00 UK");
await fire(env, "2026-10-25T10:00:00Z"); ok(calls.length === 3, "clock-change day (GMT returns) still gets a draft at 10:00 UK");
env = makeEnv(); calls = []; ghStatus = 500;
let threw = false; try { await fire(env, "2026-11-03T10:00:00Z"); } catch { threw = true; }
ok(threw && !env.STATE._m.has("daily:2026-11-03"), "GitHub error: the fire fails and the day is not marked done");
ghStatus = 204; await fire(env, "2026-11-03T10:30:00Z"); ok(calls.length === 2 && calls[1].body.inputs.mode === "new", "so the 10:30 fire retries and succeeds");
env = { ...makeEnv(), DAILY_DRAFT: "test" }; calls = [];
await fire(env, "2026-11-03T10:00:00Z");
ok(calls[0].body.inputs.dry_run === "true" && calls[0].body.inputs.refresh_token === "false" && calls[0].body.inputs.source === "cloudflare-test", "DAILY_DRAFT=test makes a TEST draft (cannot post)");
env = { ...makeEnv(), DAILY_DRAFT: "off" }; calls = [];
log = await fire(env, "2026-11-03T10:00:00Z"); ok(calls.length === 0 && log.includes("off"), "DAILY_DRAFT=off does nothing");
env = { ...makeEnv(), DRAFT_UK_HOUR: "9" }; calls = [];
await fire(env, "2026-11-03T10:00:00Z"); ok(calls.length === 0, "DRAFT_UK_HOUR=9 ignores the 10:00 UK fire");
await fire(env, "2026-11-03T09:00:00Z"); ok(calls.length === 1, "DRAFT_UK_HOUR=9 acts at 09:00 UK");

console.log(fails ? `\n${fails} FAILED` : "\nall worker checks passed"); process.exit(fails ? 1 : 0);
