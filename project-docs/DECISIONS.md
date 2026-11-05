# Decisions log

What was decided while building this, and why. Newest decisions are lower in each group. Update this file when a decision changes.

## Platform
- **Instagram API with Instagram Login, no Facebook Page.** The practice has a business Instagram account (Fdc.dental) and no Facebook Page. Posts go out as that account. The 60-day token is refreshed automatically.
- **Public GitHub repo.** Instagram must fetch images from a public URL and Pages hosts the privacy policy Meta requires. Hence no secrets or personal data in the repo.
- **Images stored in the repo (`images/`)**, not an image host such as imgbb.
- **Post images served by the Worker, deleted after 48 hours** (5 Nov 2026, `IMAGE_HOST=cloudflare`). The owner wants to make the repo private, which stops Instagram fetching images from GitHub. The image goes into the Worker's existing KV store with a 48-hour expiry, so Cloudflare deletes it with no clean-up job; Instagram keeps its own copy once posted. Chosen over R2 (needs a new bucket, and its lifecycle rules work in whole days) and imgbb (a third party). The upload is signed with the existing `LINK_SECRET`, so no new token or wider Cloudflare permission is needed. A private archive copy is still committed to `images/`. Storage cost was not a real concern (one image a day is well inside the free allowance); the deletion keeps as little public as possible.

## Content
- **Image models on Cloudflare Workers AI** (pennies a week). Image A uses FLUX.2 dev (switched from FLUX.2 klein 9B on 3 Nov 2026 for better detail; about $0.04 an image against $0.015, still mostly inside the free daily allowance). Image B uses Leonardo Lucid Origin because two FLUX.2 variants looked too similar when used together. Models can be overridden with `IMAGE_MODEL_A` and `IMAGE_MODEL_B`.
- **Two images per draft with different prompts, styles, models and people**, so the approver has a real choice.
- **Every image shows a smiling person**, because the practice wants people, not objects. The person mix is 80% white, 10% Black, 10% Asian, with random gender and age, because the models otherwise favour one look. The two people differ by age band, and a regenerated image never reuses the previous people.
- **"Bright, healthy-looking" teeth, not "perfect".** UK dental advertising rules discourage overpromising. Prompts ask for healthy, even, bright teeth and clear skin, and block cartoonish or glowing teeth. Captions keep "Imagery is illustrative. Individual results vary."
- **Ordinary clothing, no festive styling.** Early images all looked festive because the `events-season` theme asked for festive lights; it was rewritten and the shared style now blocks festive or party looks. Seasonal content is chosen deliberately through `calendar.json`.
- **Two caption choices** (Nov 2026; the Claude option was dropped at the owner's request): Choice 1 the default template, Choice 2 Cloudflare Llama 3.3 70B (300-600 characters, two thirds of the earlier length). Both pass the compliance filter. Email headings read "Instagram Caption - Choice 1/2".
- **No URLs in captions**: Instagram does not make caption links clickable, so captions point to the link in bio. `strip_urls` removes any URL a model adds anyway.
- **Seasonal calendar** mixed into the rotation (one post in three by default), with date windows and pinned awareness days. Normal themes rotate daily because the schedule is daily.

## Approval
- **Approval by HTML email**, replacing the GitHub environment approval. One approver. Sent through the practice's UK2 mailbox over SMTP, no Cloudflare email service needed.
- **Nothing posts unless approved.** If nobody approves, the draft expires after 5 days and the next scheduled draft is a separate one.
- **Signed single-use links with a confirmation page.** Mail scanners open links automatically, so a GET only shows a page and the action needs a POST confirm.
- **Limits per draft, not per week.** One approve or skip per draft (so two Approve buttons cannot post twice) and three regenerations per draft. The owner controls frequency from the schedule line in `weekly.yml`.
- **Test drafts can never post** and have their own state, so testing never blocks a real draft.
- **Email design**: summary line, side-by-side images, Instagram-style caption previews with a marker where "more" cuts off, two buttons per caption (image A or B), Outlook-safe buttons, inbox preview line, subject `Approve <weekday>'s post: <topic>`. Branding and logo were deliberately dropped.
- **Caption editing in the email** was deferred.

## Deployment
- **The Worker is deployed from GitHub** by `deploy-worker.yml` using a separate Cloudflare API token stored as a GitHub secret. The upload uses `keep_bindings` so the dashboard secrets, text settings and KV binding are not overwritten. A separate token (Workers Scripts edit only) is used rather than widening the Workers AI token.

## Schedule
- **Daily draft** at `23 10 * * *` UTC. The minute is off the hour because GitHub delays jobs at the top of the hour (a 10:00 run once started at about 16:00). GitHub still ran it 5 to 6.5 hours late and skipped 3 of 7 days in late October 2026, so the draft is now started by the Worker's Cloudflare Cron Trigger at 10:00 UK all year (fires at 09:00 and 10:00 UTC and acts only in the UK hour, so no edits at the clock changes). The GitHub cron was kept briefly as a backup, then removed on 3 Nov 2026 at the owner's request: its late runs made unwanted extra drafts and used up the Workers AI free allowance. A failed day is now handled by a manual run.
- **Waiting out Cloudflare's "daily allowance used up" refusals** (5 Nov 2026, owner's choice). On 3 and 5 Nov the 10:00 draft was refused with 429 code 4006 while the dashboard showed no usage; a manual run later the same day worked. Quick retries cannot help, so the draft waits 30, 45 and 60 minutes before giving up (`LIMIT_WAITS_MIN`, job timeout 180 minutes). Any real draft that fails emails the approver, so a missed day is noticed. Test runs never send it.

## Privacy
- The privacy policy page is `docs/privacy.html`. It mentions Cloudflare, the email provider, the approver email, 7-day draft retention and link expiry. Update it if data handling changes.
