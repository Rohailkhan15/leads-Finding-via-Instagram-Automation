# Instagram Lead Generation Automation — Python + Searlo + GitHub Actions
### (Standalone repo — shares the same Google Sheet and Searlo account as LinkedIn/Email)

## 1. What This Automation Does

Same underlying method as LinkedIn — searching Google's index of public Instagram pages via Searlo, not touching Instagram's own servers or API. No app review, no ToS exposure, same reasoning that worked for LinkedIn.

The key difference: instead of searching for job-title language ("founder," "owner"), we search for **business-operation phrases** that small Pakistani/Indian D2C sellers commonly put in their Instagram bios — "COD available," "DM to order," "WhatsApp to order." This is deliberate: these phrases are a stronger small-business signal than a job title would be, since a large brand's Instagram bio never talks like this. One include list does the filtering job here — no need for LinkedIn's two-list (role + smallness) approach.

Every run:
1. Runs several `site:instagram.com` search queries via Searlo, using business-phrase language rather than job titles.
2. Discards any result whose URL isn't actually on `instagram.com` (Google's `site:` operator isn't airtight — confirmed this with a live test) and any URL pointing to an individual post rather than a profile page.
3. Filters the remaining results by keyword — passes go to the `Instagram` tab, fails go to `Instagram-Backup`.
4. Dedupes each tab against itself only, same as LinkedIn.

This is **manually triggered**, not scheduled — same decision you made for LinkedIn, for the same reason (outreach capacity is the real bottleneck, not lead supply). Run it via `workflow_dispatch` whenever the queue needs topping up.

Region: Pakistan and India together from the start — no separate region-specific queries needed for the first pass, since COD/DM-to-order phrasing is itself a South Asia-specific signal. Niche: broad first, narrowed later once we see what the filter actually returns — same approach that worked for tuning LinkedIn.

---

## 2. Before You Start

- [ ] A **new, separate** GitHub repo (e.g. `trevolk-instagram-leadgen`)
- [ ] Your existing Searlo API key (same account as LinkedIn — no new signup needed)
- [ ] The same Google Sheet and service account already used for Email/LinkedIn
- [ ] Python 3.10+ for local testing

**Important — shared credit pool:** this reuses the *same* Searlo account as LinkedIn, meaning both automations draw from the *same* 3,000-credit/3-month pool, not separate pools each. Keep an eye on combined usage across both, not just per-automation — check your Searlo dashboard total, not just what this script alone reports.

---

## 3. Step 1 — Reuse Existing Credentials

Nothing new to sign up for:
1. Your existing **Searlo API key** — same one from the LinkedIn repo.
2. The same **Google service account JSON** and **Sheet ID** from Email/LinkedIn.

As before, these need to be pasted fresh into this new repo's own GitHub Secrets (Step 8) — secrets don't carry across repos automatically.

---

## 4. Step 2 — Add New Tabs to the Google Sheet

**Tab: `Instagram`** — same structure as the others:

| Name | Business/Profile | Platform | Date Found | Message Sent | Response | Follow-up 1 | Follow-up 2 | Status |
|---|---|---|---|---|---|---|---|---|

"Business/Profile" = the Instagram profile URL. Outreach happens via Instagram DM, done manually by Musa.

**Tab: `Instagram-Backup`** — identical columns, for filtered-out results.

---

## 5. Step 3 — Search Queries and Keyword Filters

**Search queries (dorks)** — broad first, per your call:
```
site:instagram.com "COD available"
site:instagram.com "DM to order"
site:instagram.com "WhatsApp to order"
site:instagram.com "cash on delivery" shop
site:instagram.com "free delivery" order
site:instagram.com "order now" shop
```

**Include keywords** (must appear in the bio/snippet text to pass — at least one):
`cod available, cash on delivery, dm to order, whatsapp to order, order now, shop now, nationwide delivery, free delivery, pkr, rs.`

**Exclude keywords** (auto-reject to Backup if present):
`agency, digital marketing, marketing agency, social media manager, influencer marketing, we help brands, grow your business, official account, public figure, fan page`

**URL validation (critical — confirmed necessary by a live test):**
- Must contain `instagram.com` — discard anything else, even though the query specifies `site:instagram.com`.
- Should point to a profile root (e.g. `instagram.com/username/`), not an individual post (e.g. `instagram.com/p/...`) — discard post-style URLs.

Treat all of this as a first draft. Since you're going broad intentionally this time, expect a noisier first run than LinkedIn's later iterations — that's expected, not a bug. Tighten based on what actually comes back.

---

## 6. Step 4 — Repo Structure

```
trevolk-instagram-leadgen/
├── .github/
│   └── workflows/
│       └── instagram_automation.yml
├── instagram_automation.py
├── requirements.txt
└── .gitignore
```

`requirements.txt`:
```
requests
gspread
google-auth
python-dotenv
```

`.gitignore`:
```
*.json
.env
__pycache__/
```

---

## 7. Step 5 — Script Logic (for the AI agent to build)

1. Loop through each search query in Section 5's list.
2. Call Searlo's search endpoint for each (same auth pattern as LinkedIn — `x-api-key` header, verify current endpoint against searlo.tech/docs before finalizing).
3. For each result:
   - Discard if the URL doesn't contain `instagram.com`.
   - Discard if the URL points to a post (`/p/`) rather than a profile.
   - Check the title+snippet text against include/exclude keyword lists.
   - Pass → `Instagram` tab. Fail → `Instagram-Backup` tab.
4. Read existing rows in both tabs separately; dedupe each against its own tab only (by profile URL) — no cross-check against `Instagram-Backup`, `LinkedIn`, or `Email`.
5. Append new rows: Name (best-effort from the page title, this data will be thinner than LinkedIn's — often just the account name/handle), Business/Profile (Instagram URL), Platform ("Instagram"), Date Found, then blanks.
6. Log clearly: queries run, raw results fetched, pass/fail counts, duplicates skipped, new rows added per tab.

---

## 8. Step 6 — Secrets and GitHub Actions Workflow

In this new repo's **Settings → Secrets and variables → Actions**, add:
- `SEARLO_API_KEY` → same key as LinkedIn
- `SHEET_ID` → same Sheet ID as Email/LinkedIn
- `GOOGLE_CREDS_JSON` → same service account JSON as Email/LinkedIn

**`.github/workflows/instagram_automation.yml`** — manual trigger only, no schedule (matching your LinkedIn decision):
```yaml
name: Instagram Lead Generation

on:
  workflow_dispatch: {}

jobs:
  run-automation:
    runs-on: ubuntu-latest
    steps:
      - name: Check out repo
        uses: actions/checkout@v4

      - name: Set up Python
        uses: actions/setup-python@v5
        with:
          python-version: "3.11"

      - name: Install dependencies
        run: pip install -r requirements.txt

      - name: Run Instagram automation script
        env:
          SEARLO_API_KEY: ${{ secrets.SEARLO_API_KEY }}
          SHEET_ID: ${{ secrets.SHEET_ID }}
          GOOGLE_CREDS_JSON: ${{ secrets.GOOGLE_CREDS_JSON }}
        run: python instagram_automation.py
```

---

## 9. Step 7 — The Prompt for Qoder

```
This is a brand-new, standalone repo for an Instagram lead-generation automation. It's separate from my Email and LinkedIn repos, but writes into the same Google Sheet (different tabs) and uses the same Searlo account as LinkedIn.

Build instagram_automation.py plus its GitHub Actions workflow.

Purpose: search Google (via the Searlo API) for public Instagram profiles of small e-commerce sellers in Pakistan/India, filter them by keyword, and log qualified and unqualified results into two separate Google Sheets tabs.

Requirements:

1. Authenticate to Searlo using the SEARLO_API_KEY environment variable, sent as an x-api-key header. Verify Searlo's exact current endpoint and request/response format from their live docs (searlo.tech/docs) before finalizing — don't assume the shape.

2. Run these search queries in sequence (as a configurable Python list at the top of the file):
   - site:instagram.com "COD available"
   - site:instagram.com "DM to order"
   - site:instagram.com "WhatsApp to order"
   - site:instagram.com "cash on delivery" shop
   - site:instagram.com "free delivery" order
   - site:instagram.com "order now" shop

3. For each search result, extract: the account name/handle (best effort from the title), the Instagram URL, and the snippet/bio text.
   - Discard any result whose URL does not contain "instagram.com".
   - Discard any result whose URL points to an individual post rather than a profile (e.g. contains "/p/" or "/reel/") — keep only profile-root URLs.

4. Apply a keyword filter to the combined title+snippet text:
   - INCLUDE_KEYWORDS (must contain at least one): cod available, cash on delivery, dm to order, whatsapp to order, order now, shop now, nationwide delivery, free delivery, pkr, rs.
   - EXCLUDE_KEYWORDS (if any present, auto-fail regardless of include match): agency, digital marketing, marketing agency, social media manager, influencer marketing, we help brands, grow your business, official account, public figure, fan page
   - Make both lists easy-to-edit Python constants at the top of the file.
   - Include-match AND no exclude-match = PASS. Everything else = FAIL.

5. Connect to Google Sheets using a service account (GOOGLE_CREDS_JSON, SHEET_ID env vars, same gspread + google-auth pattern as the LinkedIn script). Read from and write to two tabs: "Instagram" (PASS) and "Instagram-Backup" (FAIL).

6. Deduplicate: check each tab against its own existing rows only (by Instagram profile URL) — do not reference or touch the "LinkedIn," "LinkedIn-Backup," or "Email" tabs at all.

7. Column format for both tabs, in order: Name, Business/Profile (the Instagram profile URL), Platform ("Instagram"), Date Found (today's date), then blank strings for Message Sent, Response, Follow-up 1, Follow-up 2, Status.

8. Add clear print/log statements: total queries run, total raw results fetched, pass/fail counts, duplicates skipped, new rows added per tab.

9. No hardcoded secrets anywhere — everything from environment variables.

10. Create .github/workflows/instagram_automation.yml with ONLY a workflow_dispatch trigger — no scheduled cron. This automation is meant to be run manually whenever needed, not on a fixed schedule.

11. Also write requirements.txt (requests, gspread, google-auth, python-dotenv) and a .gitignore excluding *.json, .env, and __pycache__/.

After building, tell me exactly what secrets I need to add in this repo's GitHub settings and how to test locally with a .env file before pushing.

Do not run or execute anything yourself — I will test locally with my own credentials.
```

---

## 10. Step 8 — Test Locally Before First Real Run

1. Create a local `.env` file with `SEARLO_API_KEY`, `SHEET_ID`, `GOOGLE_CREDS_JSON` (same values as LinkedIn, pasted into this repo's own `.env`).
2. Run `python instagram_automation.py` locally.
3. Check both tabs — since this is a broad first pass, expect a noisier split than LinkedIn's tuned version. Look specifically at:
   - Are `Instagram` tab entries genuinely small sellers, or is noise leaking through?
   - Is `Instagram-Backup` catching things that seem like real leads but missed a keyword — a sign the include list needs expanding, not that the lead was bad?
4. Run it a second time immediately — confirm dedup blocks repeats.
5. Push to GitHub, add the three secrets, trigger once manually via Actions to confirm it behaves the same as local.

---

## 11. Maintenance

- [ ] Check Searlo's dashboard for **combined** LinkedIn + Instagram credit usage, not just this script's own count.
- [ ] After the first broad run, review both tabs together and decide what to tighten — likely candidates: adding city-specific terms (Lahore, Karachi, Delhi, Mumbai) if results skew too international, or adding more South Asian small-business phrases we haven't thought of yet.
- [ ] Since this is manually triggered, there's no "it silently stopped running" risk — but also no automatic reminder to run it. Pair this with the same eyeballing habit you're using for LinkedIn's queue.

---

## 12. Common Issues

| Problem | Likely Cause | Fix |
|---|---|---|
| Off-domain results despite `site:instagram.com` | Google's site: operator isn't perfectly enforced | Confirmed issue — the URL-contains-check in the script should already handle this; if not, check that logic |
| Lots of individual posts, not profiles | Missing `/p/` or `/reel/` exclusion | Verify that filter step is in place |
| Very few passes on a broad run | Include list too narrow for how sellers actually phrase bios | Expand INCLUDE_KEYWORDS with more South Asian small-business phrasing |
| Big-brand or celebrity accounts showing up | Query phrase not exclusive enough to small sellers | Add stronger exclude terms once you see specific offenders |
| Searlo credits draining faster than expected | Shared pool with LinkedIn, not separate | Check combined usage across both repos in the Searlo dashboard |
| Duplicate rows | Dedup scoped to wrong tab | Confirm URL comparison only checks the matching tab |
