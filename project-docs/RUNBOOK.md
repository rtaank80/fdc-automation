# Runbook

## Everyday
- **Draft emails** arrive from the daily run. Pick a caption and an image, press Confirm on the page that opens. Ignore a draft to post nothing.
- **Run a draft now:** Actions > "Weekly Instagram draft" > Run workflow. Leave "Dry run" on for a test (placeholder images, email marked TEST, cannot post); turn it off for a real draft. `theme_id` forces any theme from `themes.json` or `calendar.json`.
- **Change how often it runs:** edit the `cron` line in `.github/workflows/weekly.yml` (UTC).
- **Change the people mix, models, styles:** `themes.json`. **Seasonal themes and mixing:** `calendar.json`.

## Changing the Worker
1. Edit `worker/worker.js` and run `bash tests/run_all.sh`.
2. Commit to `main`. The "Deploy approval Worker" workflow uploads it to Cloudflare and checks the Worker answers. You can also start it by hand from the Actions tab.
3. If it fails or the Worker misbehaves, roll back in the Cloudflare dashboard (Worker > Deployments).

One-off setup for this: create a Cloudflare API token (My Profile > API Tokens > Create Token > "Edit Cloudflare Workers" template, limited to the account), and save it as the GitHub secret `CLOUDFLARE_DEPLOY_TOKEN`. `CLOUDFLARE_ACCOUNT_ID` is already a secret. Until the token exists, the workflow only prints a warning.

## Rotating `LINK_SECRET`
Change the GitHub secret and the Worker secret to the same new value. Links in emails sent before the change stop working.

## Troubleshooting
| Symptom | Cause and fix |
|---|---|
| Image step fails with `AiError ... 3043` or another 5xx | Temporary Cloudflare error. The code retries up to five times with waits of 15, 30, 60 and 120 seconds. If it still fails, run again later |
| Only one image in the email | One model failed after its retries; the log says which. The other image still works |
| "Invalid control character" when making a Cloudflare caption | Fixed by parsing the model's JSON with `strict=False`; if it returns, check `_parse_caption_json` |
| Scheduled run started hours late | GitHub best-effort scheduling, worst at the top of the hour. The schedule is already off the hour; a Cloudflare Cron Trigger is the fix |
| "This draft has already been decided" | The one-decision-per-draft rule. If a real draft was blocked by mistake, delete its `decided:` key in the Worker's KV namespace |
| Buttons behave like an old version or `Post with image B` posts image A | The Cloudflare copy of the Worker was not updated. Check the latest "Deploy approval Worker" run (missing `CLOUDFLARE_DEPLOY_TOKEN` only gives a warning) |
| Email does not arrive | Check the SMTP secrets and `SMTP_PORT` (587 STARTTLS by default; 465 for SSL), the junk folder, and the generate job log for "Approval email sent" |
| `Token refresh failed` on the first run | A token must be 24 hours old before it can be refreshed; it resolves itself |
| Instagram error "Only photo or video can be accepted" | The image URL must be a public JPEG; check the repo is public |
| Meta "You can't make this change at the moment" | A temporary Meta security block; wait, or use a trusted device and network |

## Checks after a deploy
1. Run a dry-run draft and confirm the TEST email arrives with both images and the buttons.
2. Press Approve with image B on the TEST draft. The publish run should log `DRY RUN` and `with image B`.
3. For a first real run, watch that the post appears on the account and that the image is the one chosen.
