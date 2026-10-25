# FDC weekly Instagram automation

Every week this prepares a post for **@fdc.dental**: an AI-generated cosmetic dentistry / whitening image and up to three caption options, with a link to the website. The approver gets an email, picks a caption with one button, and only then does it post.

```
GitHub Actions cron (Tuesday 09:00 UTC)  "Weekly Instagram draft"
  -> pick theme -> up to 3 captions (1 template, 2 Claude, 3 Cloudflare) + an image
  -> email to the approver: the image, each caption with its own Approve button, and
     "new captions", "new image", "new everything" and "skip" buttons
  -> a button opens a confirmation page (Cloudflare Worker); pressing Confirm acts:
        Approve  -> "Publish approved post" workflow -> Instagram
        New ...  -> a fresh draft email (3 per draft at most)
        Skip     -> nothing is posted
  -> no click at all = nothing is posted; links expire after 5 days; the next draft comes next Tuesday
```

Cost is a few pence a week at most. GitHub Actions and the Cloudflare Worker are free at this volume.

## One-off setup

### 1. Put the project on GitHub
Create a **public** repo and upload this folder's contents (public is needed so Instagram can fetch the post image and for the free privacy page; secrets stay private). Check the hidden `.github/workflows/` folder uploaded: the Actions tab should list *Weekly Instagram draft* and *Publish approved post*. Under Settings > Actions > General > Workflow permissions choose **Read and write permissions**.

### 2. Get your Instagram token (no Facebook Page needed)
Uses Meta's **Instagram API with Instagram Login**. Follow these in order:
1. **Account:** @fdc.dental must be a Business or Creator account (Instagram app > Settings > Account type and tools).
2. **Developer login:** sign in at developers.facebook.com with a personal Facebook account and register as a developer.
3. **Create the app:** My Apps > Create app. Choose the *Business* app type (other types will not show the right Instagram options). Give it a name such as "FDC Instagram Poster".
4. **Add Instagram:** in the app dashboard add the **Instagram** product, then open **Instagram > API setup with Instagram business login**.
5. **Connect the account:** on that page, add @fdc.dental if it is not listed (it will ask you to sign in to Instagram). If Meta says the account needs a role, add it under App roles as an Instagram tester and accept the invite in the Instagram app (Settings > Website permissions > Apps and websites > Tester invites).
6. **Generate token:** click **Generate token** next to @fdc.dental, sign in to Instagram, approve the permissions (`instagram_business_basic` and `instagram_business_content_publish`) and copy the token. Tokens made this way are already long-lived (60 days), so no exchange step is needed.
7. **Verify it:** open `https://graph.instagram.com/v25.0/me?fields=user_id,username&access_token=YOUR_TOKEN`. It should return `@fdc.dental`'s username. If the username is wrong, the token belongs to the wrong account.
8. Posting to your own account works with Standard Access, so you can leave the app in development mode and skip App Review.

### Privacy policy page (required by Meta)
Meta asks for a public Privacy Policy URL (and a Data Deletion URL) before an app can go live. `docs/privacy.html` is a ready-made policy for this app, covering the approval email and Worker.
1. In your GitHub repo go to Settings > Pages, set Source to *Deploy from a branch*, branch `main`, folder `/docs`, and save.
2. After a minute the page is at `https://YOUR-USERNAME.github.io/YOUR-REPO/privacy.html`.
3. In the Meta app dashboard go to App settings > Basic and paste that URL into **Privacy Policy URL**. For **User data deletion** choose *Data deletion instructions URL* and paste the same URL followed by `#data-deletion`.
4. Check the contact details and wording are right for the practice before publishing.

### 3. Image and caption keys
- **Cloudflare Workers AI** (image: FLUX.2 klein 9B; caption option 3: Llama 3.3 70B). Both fit in the free daily allowance at one post a week.
  1. Sign up free at dash.cloudflare.com.
  2. Open **AI > Workers AI**, choose **Use REST API**, and copy your **Account ID**.
  3. Click **Create a Workers AI API Token**, accept the prefilled settings, create it and copy the token.
  4. Check Black Forest Labs' terms (bfl.ai/legal/terms-of-service) allow commercial use for your case.
  To use OpenAI for images instead, set the repo variable `IMAGE_PROVIDER=openai` and add the secret `OPENAI_API_KEY`.
- **Anthropic API key** (optional): adds caption option 2 (Claude). Without it the email offers options 1 and 3.
- **Image hosting:** nothing to set up. Each approved image is saved into the repo's `images/` folder. To use imgbb instead, set the variable `IMAGE_HOST=imgbb` and add the secret `IMGBB_API_KEY`.

### 4. A GitHub token so the Worker can start workflows
1. GitHub > your profile picture > Settings > Developer settings > Personal access tokens > **Fine-grained tokens** > Generate new token.
2. Name: `FDC approvals Worker`. Expiration: the longest offered (put a renewal reminder in your calendar). Repository access: **Only select repositories** > this repo.
3. Under Repository permissions set **Actions: Read and write** and nothing else. Generate and copy it (shown once). This goes into Cloudflare in step 6, **not** into GitHub.

### 5. Add secrets and variables in GitHub
Repo > Settings > Secrets and variables > Actions.

**Secrets** (New repository secret):
| Name | Value |
|---|---|
| `CLOUDFLARE_ACCOUNT_ID`, `CLOUDFLARE_API_TOKEN` | from step 3 |
| `ANTHROPIC_API_KEY` | optional |
| `IG_ACCESS_TOKEN` | from step 2 |
| `GH_PAT` | a fine-grained token with **Secrets: Read and write** on this repo; lets the weekly job save the refreshed Instagram token (without it the token must be replaced by hand before day 60) |
| `SMTP_USER` | the full sending mailbox address, e.g. `fdc-dental.instagram@family-dental-centre.co.uk` |
| `SMTP_PASSWORD` | that mailbox's password |
| `APPROVER_EMAIL` | who receives the weekly draft, e.g. `ritesh.taank@yahoo.com` |
| `LINK_SECRET` | a long random string (a password manager, or `openssl rand -hex 32`). **Must be identical** to the Worker's `LINK_SECRET` |

**Variables** (Variables tab):
| Name | Value |
|---|---|
| `APPROVAL_BASE_URL` | the Worker's address from step 6, e.g. `https://fdc-approvals.yourname.workers.dev` |
| `SMTP_HOST` | optional, defaults to `smtp.livemail.co.uk` |
| `SMTP_PORT` | optional, defaults to `587` (STARTTLS); use `465` if UK2 says SSL |

### 6. Create the approval Worker in Cloudflare
(Screen labels change from time to time; these are the usual names.)
1. dash.cloudflare.com > **Workers & Pages** > **Create** > start from the *Hello World* template, name it `fdc-approvals`, **Deploy**.
2. Click **Edit code**, delete the sample, paste in the whole of `worker/worker.js` from this project, and **Deploy**.
3. Create the memory it uses: **Storage & databases > KV > Create** a namespace named `fdc-approvals-state`.
4. Back in the Worker: **Settings > Bindings > Add > KV namespace**. Variable name `STATE` (exactly), pick the namespace, save.
5. **Settings > Variables and secrets > Add**:
   - `GH_REPO` (type Text): `your-username/your-repo`
   - `GH_TOKEN` (type Secret): the token from step 4
   - `LINK_SECRET` (type Secret): the same value as in GitHub
   - optional Text values: `GH_REF` (branch, default `main`), `MAX_REGENS` (default `3`)
6. Under **Settings > Domains & Routes** make sure the `workers.dev` address is enabled, copy it, and put it in the GitHub variable `APPROVAL_BASE_URL`. Visiting that address should say "The approval service is running."

### 7. Test (nothing real is posted)
1. Actions > *Weekly Instagram draft* > **Run workflow** with *Dry run* ticked. A `[TEST]` email arrives at the approver address (check spam the first time; mark it "not spam").
2. Click **Approve option 1**, then **Confirm**. The *Publish approved post* workflow starts and logs `DRY RUN`. Test drafts can never post, have no limits (so you can click approve, skip and regenerate as often as you like) and don't affect real drafts.
3. When happy, run the first draft for real (*Dry run* unticked), approve a caption, and check @fdc.dental.
4. After that it runs by itself every Tuesday. Disable it any time under Actions > the workflow > **...** > Disable workflow.

## Using the email
- **Approve option N and post**: opens a confirmation page; **Confirm** starts the post (usually live within a couple of minutes).
- **Generate new captions / new image / both**: a fresh email follows in a few minutes. Three of these per draft at most; the email shows how many are left.
- **Skip this draft**: nothing posts.
- Each link works once, and only until its expiry shown in the email. Once a caption is approved or skipped, the rest of that draft's links (including its regenerations) stop working. There is no limit per week: each scheduled run is a separate draft, so how often posts go out is set by the schedule in `weekly.yml`, not by the Worker.
- **Fallback without the email:** Actions > *Publish approved post* > Run workflow. Enter the run ID of the draft run (the number at the end of its URL) and the option number.

## Customising
- `themes.json`: brand tone, image style and the 12 rotating topics. Edit freely, or force one with `THEME_ID`.
- **Who appears in the images:** image models tend to favour one look, so for the themes that show a person (those with `{person}` in their `image` text) the script picks a random person each run, such as "a woman in her 50s of Indian heritage", and states it in the image prompt. The lists it draws from (`genders`, `ages`, `backgrounds`) are in the `people` section of `themes.json`; add, remove or repeat entries to change the mix (repeating an entry makes it more likely). A regenerated image never repeats the previous person, and "new captions" keeps the same image and person. The chosen person is saved in the draft and shown in the run log (`Person in the image: ...`). To make another theme show a person, put `{person}` in its `image` text, for example `"A portrait of {person} smiling naturally"`.
- Schedule: edit the `cron` line in `.github/workflows/weekly.yml` (times are UTC). Add a second `- cron:` line for a second draft in the week; each run is approved separately.
- The caption instructions and the default template are in `post_weekly.py` (`_prompts`, `fallback_caption`); banned wording is the `BANNED` list.
- Local run: `pip install -r requirements.txt`, copy `.env.example` to `.env`, export the variables, then `python post_weekly.py generate --dry-run` (open `out/email-preview.html` to see the email).

## Compliance notes (UK dental advertising)
- Images are deliberately illustrative, with no before/after pairs and no fake patient results; the caption states "Imagery is illustrative. Individual results vary." (matching your website).
- Captions avoid guarantees, superlatives, prices and discounts; any AI caption containing risky wording is dropped. The approver should still read each caption.
- Check the GDC advertising guidance and ASA/CAP rules, and have a clinician at the practice skim the first few drafts.
- Instagram captions can't hold clickable links; the caption points to "link in bio", so set your bio link to the website.

## Security notes
- An email button is a link: anyone who can open the email could use it until it expires or is used. Send it only to a mailbox you control, and don't forward it.
- Links are signed (tampering is rejected), expire after 5 days and work once. Opening a link only shows a page; the Confirm press is what acts, so mail scanners can't approve anything.
- The repo is public, so the draft files kept as workflow artifacts (7 days) can be downloaded by any signed-in GitHub user while they exist, and approved images stay in `images/`. Nothing in them is sensitive beyond unapproved marketing text and images.
- Keep the Worker's GitHub token limited to **Actions: Read and write** on this repo only.

## Troubleshooting
- *No email:* check the draft run's log for the SMTP message. "Login rejected": use the full address in `SMTP_USER` and check the password. Connection errors: try `SMTP_PORT=465`.
- *"Link is invalid or has expired":* it expired, or `LINK_SECRET` differs between GitHub and the Worker (they must match exactly).
- *"GitHub did not accept the request":* check the Worker's `GH_REPO` spelling, that `GH_TOKEN` has **Actions: Read and write** on that repo, and that both workflow files are on the `main` branch.
- *Approved but nothing posted:* open the *Publish approved post* run and read its log. "No unexpired draft artifact" means the draft is over 7 days old.
- *The people in the images still look too alike, or a group comes out stereotyped:* the model treats the description as guidance, not a guarantee. Check the `Person in the image` line in the run log against what you see, use **Generate a new image** in the email, and tell the maintainer which description misbehaved so the wording in `post_weekly.py` can be adjusted.
- *Error 9007 / media not ready:* the script polls for it; re-run if the image host was slow.
- *"Only photo or video can be accepted":* the image URL must be a public JPEG; check the repo is public.
- *Token errors:* check the token has the `instagram_business_content_publish` permission and the account is an Instagram tester on the app.
- *"Token refresh failed" warning on the first run:* a token must be at least 24 hours old before it can be refreshed. It resolves itself the next week.
