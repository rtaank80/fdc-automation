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
Uses Meta's **Instagram API with Instagram Login**. Follow these in order:
1. **Account:** @fdc.dental must be a Business or Creator account (Instagram app > Settings > Account type and tools).
2. **Developer login:** sign in at developers.facebook.com with a personal Facebook account and register as a developer.
3. **Create the app:** My Apps > Create app. Choose the *Business* app type (other types will not show the right Instagram options). Give it a name such as "FDC Instagram Poster".
4. **Add Instagram:** in the app dashboard add the **Instagram** product, then open **Instagram > API setup with Instagram business login**.
5. **Connect the account:** on that page, add @fdc.dental if it is not listed (it will ask you to sign in to Instagram). If Meta says the account needs a role, add it under App roles as an Instagram tester and accept the invite in the Instagram app (Settings > Website permissions > Apps and websites > Tester invites).
6. **Generate token:** click **Generate token** next to @fdc.dental, sign in to Instagram, approve the permissions (`instagram_business_basic` and `instagram_business_content_publish`) and copy the token. Tokens made this way are already long-lived (60 days), so no exchange step is needed.
7. **Verify it:** run `curl "https://graph.instagram.com/v25.0/me?fields=user_id,username&access_token=YOUR_TOKEN"`. It should return `@fdc.dental`'s username. If the username is wrong, the token belongs to the wrong account.
8. Posting to your own account works with Standard Access, so you can leave the app in development mode and skip App Review.

### Privacy policy page (required by Meta)
Meta asks for a public Privacy Policy URL (and a Data Deletion URL) before an app can go live. `docs/privacy.html` is a ready-made policy for this app.
1. In your GitHub repo go to Settings > Pages, set Source to *Deploy from a branch*, branch `main`, folder `/docs`, and save.
2. After a minute the page is at `https://YOUR-USERNAME.github.io/YOUR-REPO/privacy.html` (GitHub Pages on a private repo needs a paid plan; use a public repo, which is safe because secrets are never in the code).
3. In the Meta app dashboard go to App settings > Basic and paste that URL into **Privacy Policy URL**. For **User data deletion** choose *Data deletion instructions URL* and paste the same URL followed by `#data-deletion`.
4. Check the contact details and wording in the file are right for the practice before publishing.

### 3. Other keys
- **Cloudflare Workers AI** for image generation (FLUX.2 klein 9B, free tier covers one image a week):
  1. Sign up free at dash.cloudflare.com.
  2. Open **AI > Workers AI**, choose **Use REST API**, and copy your **Account ID**.
  3. Click **Create a Workers AI API Token**, accept the prefilled settings (needs "Workers AI - Read" and "Edit"), create it and copy the token.
  4. Add GitHub secrets `CLOUDFLARE_ACCOUNT_ID` and `CLOUDFLARE_API_TOKEN`.
  5. Check Black Forest Labs' terms (bfl.ai/legal/terms-of-service) allow commercial use for your case.
  To use OpenAI instead, add the repo variable `IMAGE_PROVIDER=openai` and the secret `OPENAI_API_KEY`.
- **Anthropic API key** for captions (optional; without it a safe template caption is used).
- **Image hosting:** nothing to set up. Instagram needs a public image URL, so the workflow saves each image into the `images/` folder of this repo (which must be **public**) using GitHub's built-in token. To use imgbb instead, set the repo variable `IMAGE_HOST=imgbb` and add the secret `IMGBB_API_KEY`.

### 4. Add them to GitHub
Settings > Secrets and variables > Actions > New repository secret:
`CLOUDFLARE_ACCOUNT_ID`, `CLOUDFLARE_API_TOKEN`, `ANTHROPIC_API_KEY`, `IG_ACCESS_TOKEN`, `GH_PAT`.

**Keeping the token alive:** Instagram tokens last 60 days. The workflow refreshes the token every week and saves the new one back into the `IG_ACCESS_TOKEN` secret, so it never expires as long as the automation keeps running. For this it needs one more secret, `GH_PAT`: create a fine-grained personal access token (GitHub > Settings > Developer settings) limited to this repository with **Secrets: Read and write** permission. Without it the posts still work, but you must replace the token by hand before day 60, and the run will show a warning.

### 5. Turn on approval (recommended)
Settings > Environments > New environment named **`instagram-approval`** > tick *Required reviewers* and add yourself. Each week you get an email; open the run, read the draft in the summary, click **Approve**. Leave it without reviewers and it posts fully automatically.

### Choosing between caption options
Each week the draft offers up to three captions, and the reviewer picks one when approving:
- **Option 1: Default template** (always available; fixed wording from `post_weekly.py`)
- **Option 2: Claude** (only if the `ANTHROPIC_API_KEY` secret is set)
- **Option 3: Cloudflare** (uses your Cloudflare keys; model `@cf/meta/llama-3.3-70b-instruct-fp8-fast`, change it with the repo variable or env `CAPTION_CF_MODEL`)

An option is left out if its service fails or its caption contains banned wording. To choose: open the run on the Actions tab, read the options in the run summary, click **Review deployments**, type `1`, `2`, `3` (or `claude`, `cloudflare`, `template`) in the comment box and click **Approve**. With no comment, the default is Claude, then Cloudflare, then the template. This needs the `instagram-approval` environment with a required reviewer (step 5 above); without one, the default caption is posted automatically.

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
