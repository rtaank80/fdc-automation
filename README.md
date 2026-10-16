# FDC weekly Instagram automation

Every week this posts an AI-generated cosmetic dentistry / whitening image to **@fdc.dental**, with a caption written in the practice's tone and a link to the website, to drive visits and enquiries.

```
GitHub Actions cron (Tue 09:00 UTC)
  -> generate: pick theme -> write caption (Claude) -> create image (OpenAI) -> draft saved
  -> refresh the Instagram token (keeps it valid)
  -> approval gate (optional, recommended): you tap "Approve" on GitHub
  -> publish: host image -> Instagram Graph API -> live post
```

Cost is roughly a few pence per post for the image and caption. GitHub Actions is free at this volume.

## One-off setup

### 1. Put the project on GitHub
Create a repo, push this folder to it. Use a **public** repo if you want the free approval gate (private repos need a paid GitHub plan for required reviewers). Secrets stay private either way.

### 2. Get your Instagram token (no Facebook Page needed)
This uses Meta's **Instagram API with Instagram Login**.
1. In the Instagram app, make sure @fdc.dental is a **Business or Creator** account (Settings > Account type and tools).
2. Go to developers.facebook.com, create an app (type: Business) and add the **Instagram** product, choosing "API setup with Instagram login".
3. In the app's Instagram settings, **add your Instagram account** (role: Instagram tester). Accept the invite in the Instagram app under Settings > Website permissions > Apps and websites > Tester invites.
4. Click **Generate token** for @fdc.dental and approve the permissions `instagram_business_basic` and `instagram_business_content_publish`. Copy the token. If Meta gives you a short-lived token, exchange it for a long-lived one (valid 60 days) using `GET https://graph.instagram.com/access_token?grant_type=ig_exchange_token&client_secret=APP_SECRET&access_token=SHORT_TOKEN`.
5. Posting to your own account works while the app is in development mode; no App Review needed.

### 3. Other keys
- **OpenAI API key** for image generation (`gpt-image-1`).
- **Anthropic API key** for captions (optional; without it a safe template caption is used).
- **imgbb API key** (free, imgbb.com) because Instagram needs a public image URL. Alternatively set variable `IMAGE_HOST=github` on a public repo.

### 4. Add them to GitHub
Settings > Secrets and variables > Actions > New repository secret:
`OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `IG_ACCESS_TOKEN`, `IMGBB_API_KEY`.

**Keeping the token alive:** Instagram tokens last 60 days. The workflow refreshes the token every week and saves the new one back into the `IG_ACCESS_TOKEN` secret, so it never expires as long as the automation keeps running. For this it needs one more secret, `GH_PAT`: create a fine-grained personal access token (GitHub > Settings > Developer settings) limited to this repository with **Secrets: Read and write** permission. Without it the posts still work, but you must replace the token by hand before day 60, and the run will show a warning.

### 5. Turn on approval (recommended)
Settings > Environments > New environment named **`instagram-approval`** > tick *Required reviewers* and add yourself. Each week you get an email; open the run, read the draft in the summary, click **Approve**. Leave it without reviewers and it posts fully automatically.

### 6. Test
Actions tab > *Weekly Instagram post* > **Run workflow** with *Dry run* ticked. Then run once with it unticked to make the first real post.

## Customising
- `themes.json`: brand tone, image style and the 12 rotating topics (about a quarter of the year per cycle). Edit freely, or force one with `THEME_ID`.
- Schedule: edit the `cron` line in `.github/workflows/weekly.yml` (times are UTC).
- Local run: `pip install -r requirements.txt`, copy `.env.example` to `.env`, export the variables, then `python post_weekly.py generate --dry-run`.

## Compliance notes (UK dental advertising)
- Images are deliberately illustrative, with no before/after pairs and no fake patient results; the caption states "Imagery is illustrative. Individual results vary." (matching your website).
- Captions avoid guarantees, superlatives, prices and discounts; the script rejects any caption containing risky wording and falls back to a template.
- Check the GDC advertising guidance and ASA/CAP rules, and have a clinician at the practice skim the first few drafts via the approval step.
- Instagram captions can't hold clickable links; the caption points to "link in bio", so set your bio link to the website.

## Troubleshooting
- *Error 9007 / media not ready*: the script polls for it; re-run if the image host was slow.
- *"Only photo or video can be accepted"*: the image URL must be public JPEG; check `IMAGE_HOST` and its key.
- *Token errors*: check the token was generated with the `instagram_business_content_publish` permission and that your account is added as an Instagram tester on the app.
- *"Token refresh failed" warning on the first run*: a token must be at least 24 hours old before it can be refreshed. It resolves itself the next week.
