# Backlog

Priority: P1 protects what exists or fixes a gap, P2 worth doing next, P3 nice to have or needs something from the owner first.
Status as of 1 November 2026.

## Product enhancements
| # | Enhancement | Priority | Effort | Status |
|---|---|---|---|---|
| 1 | Failure, reminder and confirmation emails (run failed, draft not approved after 2 days, post published with link) | P1 | Small | To do |
| 4 | Weekly results report (reach, saves, likes, profile visits per post, emailed) | P1 | Medium | To do |
| 8 | Seasonal calendar in the theme rotation | P1 | Small | **Done** (`calendar.json`; dates need an annual check) |
| 2 | Instagram token health check | P2 | Small | To do |
| 3 | Automatic image check by Claude before the email (odd teeth, extra fingers, stray text) | P2 | Medium | To do |
| 5 | Theme rotation weighted by results | P2 | Medium | To do (needs #4) |
| 6 | Booking tracking (tagged link in bio, "how did you hear about us?") | P2 | Small | To do |
| 12 | Edit captions inside the email | P2 | Medium | To do |
| 7 | Carousel option (A and B together) | P3 | Medium | To do |
| 9 | Mix in real practice photos | P3 | Medium | To do (needs photos and consent) |
| 11 | Cross-posting; Google Business Profile on its own slower schedule | P3 | Medium | To do (needs a verified profile; API access unconfirmed) |
| 10 | Stories and Reels | P3 | Large | To do |

## Email design
Done: compact grid with two buttons per caption, summary line, Instagram-style preview, Outlook-safe buttons, inbox preview line, subject line, side-by-side images. Partly done: cleaner caption cards (character count missing), quieter regeneration buttons. To do: dark mode (P2), compliance reminder line (P2). Dropped: branding and logo.

## Platform and operations
| Item | Priority | Status |
|---|---|---|
| Run the schedule from a Cloudflare Cron Trigger (reliable timing, 10:00 UK all year) | P2 | To do |
| Deploy `worker/worker.js` automatically from GitHub | P2 | **Built** (`deploy-worker.yml`); needs the `CLOUDFLARE_DEPLOY_TOKEN` secret and a first run to confirm |
| Confirm Lucid Origin response handling and image quality over many runs | P2 | To do |
| Review the first real posts end to end | P1 | To do |
