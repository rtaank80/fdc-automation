# FDC Instagram automation

Automated Instagram posting for **Fdc.dental** (Family Dental Centre cosmetics and whitening, 404 Kingstanding Road, Birmingham B44 8LD, website https://fdc-invisalign-cosmetic.lovable.app/). Each day it prepares a draft (AI images and up to three captions), emails it to one approver, and posts to Instagram only after the approver clicks Approve. Nothing is ever posted without an approval click.

This file is the starting point for any Claude Code session. Longer background is in `project-docs/` (ARCHITECTURE, DECISIONS, BACKLOG, RUNBOOK). The owner-facing setup guide is `README.md`.

## The repo is public
GitHub Pages serves `docs/privacy.html` (needed for the Meta app) and Instagram must fetch post images from `images/`, so the repo has to stay public. Therefore:
- Never commit secrets, tokens, `.env`, account IDs, or personal email addresses. Use placeholders such as `approver@example.com`.
- Real values live in GitHub Actions secrets and variables, and in the Cloudflare Worker settings. Names are listed in `project-docs/ARCHITECTURE.md`.

## Commands
```
pip install -r requirements.txt
bash tests/run_all.sh                       # all offline tests (Python 3.10+, Node 18+); no network, nothing is posted
python post_weekly.py generate --dry-run    # placeholder images, no AI calls; writes out/email-preview.html
```
The tests are also run by `.github/workflows/ci.yml` on every push. Always run them after changing code, and add or update a test for any behaviour you change.

## Layout
| Path | Purpose |
|---|---|
| `post_weekly.py` | Main program. Stages: `generate` (themes, captions, two images, email), `publish` (post the approved choice), `refresh` (Instagram token) |
| `approval_email.py` | Builds and sends the approval email; signs the button links |
| `worker/worker.js` | Cloudflare Worker: confirmation page and GitHub workflow dispatch for each button; its Cron Trigger starts the daily draft |
| `themes.json` | Brand, image styles, models, people mix, the 12 normal themes |
| `calendar.json` | Seasonal themes by month; mixing and rotation settings |
| `.github/workflows/weekly.yml` | Makes the draft. Started daily by the Worker's Cron Trigger, by the Worker for regenerations, or by hand; its own GitHub schedule is a backup that stands down if the Cloudflare draft already ran |
| `.github/workflows/publish.yml` | Posts an approved draft (started by the Worker) |
| `.github/workflows/ci.yml` | Runs the tests |
| `.github/workflows/deploy-worker.yml` | Deploys `worker/worker.js` to Cloudflare when it changes on `main`, and sets the Worker's Cron Trigger |
| `tests/` | `test_flow.py` (generate, email, publish), `test_content.py` (people, themes, models, retries), `worker.test.mjs` |
| `docs/privacy.html` | Privacy policy for Meta, hosted with GitHub Pages |
| `images/` | Posted images are committed here by the publish job |

## Rules that must not be broken
1. **No post without approval.** Test drafts (`dry_run`, link payload `x=1`) can never post; `publish` refuses them whatever flags are passed. Expired drafts refuse too.
2. **UK dental advertising compliance.** Captions pass a banned-words check and carry "Imagery is illustrative. Individual results vary." Do not promise results or use guarantee, cure or painless claims. Images show healthy, bright, natural-looking teeth, deliberately not "perfect" or glowing.
3. **Every image shows a smiling or laughing person**, picked with the mix in `themes.json` (white 80, Black 10, Asian 10), a different person for image A and B, never repeating the previous draft's people, ordinary everyday clothes, no festive or party styling.
4. **Links are signed** (HMAC-SHA256) and single use. Do not weaken verification, expiry (5 days) or the confirm-page step that stops mail scanners acting on links.
5. **Limits are per draft, not per week.** One approve or skip per draft, three regenerations per draft. How often drafts are made is set only by the daily schedule (the Worker's Cron Trigger, with the `weekly.yml` schedule as a backup).
6. Keep British English and a calm, factual tone in anything patient-facing.

## Deployment notes
- GitHub: pushing to `main` updates the workflows and Python code immediately.
- Cloudflare: pushing a change to `worker/worker.js` on `main` runs `.github/workflows/deploy-worker.yml`, which uploads it to the Worker through the Cloudflare API and keeps the Worker's existing secrets, settings and KV binding (`keep_bindings`). It needs the GitHub secrets `CLOUDFLARE_ACCOUNT_ID` and `CLOUDFLARE_DEPLOY_TOKEN` (a token with Account > Workers Scripts > Edit, separate from the Workers AI token). It works out the Worker name from `APPROVAL_BASE_URL`, or from the optional variable `CLOUDFLARE_WORKER_NAME`. Until the token secret exists the workflow only warns and does not deploy. If a deploy breaks the Worker, roll back in the Cloudflare dashboard under the Worker's Deployments.
- The Worker's settings (`LINK_SECRET`, `GH_TOKEN`, `GH_REPO`, `GH_REF`, `MAX_REGENS`, optional `DAILY_DRAFT` live/test/off and `DRAFT_UK_HOUR`, KV binding `STATE`) are configured in the dashboard, not in the repo. Do not overwrite them with a config file without keeping them.
- Schedule: the Worker's Cloudflare Cron Trigger (`0,30 9,10 * * *` UTC, set by `deploy-worker.yml`) starts `weekly.yml` at 10:00 UK time (GMT and BST), once per UK day (KV key `daily:<date>`), with a :30 retry. It also requests the Instagram token refresh. The GitHub cron `23 10 * * *` stays as a backup: its `backup-check` job skips the draft if a "Daily draft (Cloudflare schedule)" run already ran today. GitHub's own schedule was hours late and sometimes skipped (3 of 7 days in late Oct 2026). Whether to remove the GitHub backup is to be decided once the Cloudflare trigger has run reliably; manual runs (dry run or live) stay either way.

## Working with the owner
- The owner (RT) is a senior security architect, not hands-on with this codebase. Explain steps plainly, and whenever files change list exactly which files changed and where they go in the repo.
- Previously the owner uploaded files through the GitHub web page, so a clear "replace these files" list matters whenever changes are not pushed directly.
- Offer a preview (screenshots) for visual changes such as the email before anything is deployed.

## Known gaps and unverified points
- Image quality and diversity from the real models are not yet reviewed over many runs. Lucid Origin (image B) request format is implemented from its documentation and its response parsing has not been confirmed against the live API.
- Instagram posting has not been confirmed end to end on a real approved draft in this version of the code; check the first real post.
- Dates and wording of the seasonal calendar came from general knowledge and need a check each year.
- See `project-docs/BACKLOG.md` for the prioritised list of enhancements.
