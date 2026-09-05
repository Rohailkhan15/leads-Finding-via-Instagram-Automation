"""
Instagram Lead-Generation Automation (standalone repo)
======================================================

Searches Google's public index of Instagram profile pages via the Searlo API,
filters the results for small e-commerce sellers (Pakistan / India), and logs
them into two tabs of one Google Sheet:

    * "Instagram"        -> results that PASS the keyword filter
    * "Instagram-Backup" -> results that FAIL (parked for manual review)

Unlike the LinkedIn automation, the signal here is not a job title but the
business-operation phrasing small D2C sellers put in their bios ("COD
available", "DM to order", "WhatsApp to order"). A large brand's bio never
talks like this, so a single INCLUDE list does the filtering job that took
LinkedIn two lists.

This repo is independent of the Email and LinkedIn automations. It writes into
the SAME spreadsheet but only ever touches the two tabs above -- it never
reads, writes, or references the "Email", "LinkedIn" or "LinkedIn-Backup" tabs.

Nothing here talks to Instagram's servers -- it only reads public Google
search results, so Instagram's Terms of Service are not implicated.

IMPORTANT -- how handles are recovered (learned from two live runs)
-------------------------------------------------------------------
Bare-phrase dorks like 'site:instagram.com "COD available"' return almost
nothing but REEL pages. A live run gave 60 results of which 59 were
instagram.com/reel/<id>/ URLs. Google indexes reels heavily because the caption
is what matches the phrase, and a reel URL contains no handle -- its SERP title
is just the caption ('COD Available - Instagram'). Title-based recovery
therefore rescued only 2 of 59.

Two changes fix that:

1. QUERIES. Requiring "Instagram photos and videos" -- the literal suffix of a
   profile page's <title> -- steers Google to profile roots instead. Those come
   back as 'COD AVAILABLE (@the__fashzone) - Instagram' with the handle in the
   title and the bio in the snippet.

2. HANDLE EXTRACTION from every available field, in priority order:
     a. URL path   -- instagram.com/<handle>/, /<handle>/reels/,
                      /<handle>/p/<id>/, m.instagram.com/_u/<handle>
     b. SERP title -- '@handle on Instagram:', 'Name (@handle) - Instagram'
     c. snippet    -- Instagram's post meta description,
                      '- name (@handle) on August 12, 2024: "caption"'
   Whatever the source, the URL is canonicalized to
   https://www.instagram.com/<handle>/ so every variant of one account
   (www., m., www-fallback., /reels/, /p/<id>/) dedups to a single row.

All secrets come from environment variables:
    SEARLO_API_KEY     -- Searlo API key (starts with "sk_")
    SHEET_ID           -- Google Sheet ID (between /d/ and /edit in the URL)
    GOOGLE_CREDS_JSON  -- full contents of the service-account JSON key

Searlo endpoint verified against https://searlo.tech/docs:
    Base URL  : https://api.searlo.tech/api/v1
    Endpoint  : GET /search/web   (current; "/search/simple" is now legacy)
    Auth      : "x-api-key" header
    Params    : q (required, 1-500 chars), limit (1-10), page, gl, hl, safe, lr
    Response  : { "success": true,
                  "searchInformation": {totalResults, query, hasNextPage, ...},
                  "items": [ {rank, title, link, snippet, domain, displayLink} ] }
    Cost      : 1 credit per request (per page, so PAGES_PER_QUERY matters)
The parser below also understands the legacy {"data": {"results": [...]}}
shape so the script keeps working even if the envelope shifts.

Note on credits: this automation shares ONE Searlo account (and therefore one
credit pool) with the LinkedIn automation. Check combined usage in the Searlo
dashboard, not just what this script reports.
"""

import json
import os
import re
import sys
import time
from datetime import date
from urllib.parse import urlparse

import requests
from dotenv import load_dotenv
import gspread
from gspread.exceptions import WorksheetNotFound
from google.oauth2.service_account import Credentials

# Load a local .env if present (used for local testing only; harmless in CI
# where the values are already provided as real environment variables).
load_dotenv()

# Windows consoles default to a legacy codepage (cp1252). Pakistani and Indian
# Instagram titles routinely contain Urdu/Hindi text and emoji, which would
# raise UnicodeEncodeError out of a plain print() and abort the run halfway.
# Force UTF-8 and replace anything unencodable so logging can never crash the
# automation. No-op on Linux/GitHub Actions, which is already UTF-8.
if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (ValueError, OSError):
        pass


# ----------------------------------------------------------------------------
# CONFIG -- edit these lists freely; the logic below does not need touching.
# ----------------------------------------------------------------------------

# Google dorks to run, in sequence. Add / remove / reword as needed.
SEARCH_QUERIES = [
    # --- Profile-biased ----------------------------------------------------
    # "Instagram photos and videos" is the literal suffix of an Instagram
    # PROFILE page title ('Name (@handle) \u2022 Instagram photos and videos').
    # Requiring it steers Google away from the handle-less reel pages that
    # dominate the bare-phrase dorks further down.
    'site:instagram.com "COD available" "Instagram photos and videos"',
    'site:instagram.com "DM to order" "Instagram photos and videos"',
    'site:instagram.com "WhatsApp to order" "Instagram photos and videos"',
    'site:instagram.com "cash on delivery" "Instagram photos and videos"',
    'site:instagram.com "free delivery" "Instagram photos and videos"',
    'site:instagram.com "order now" "Instagram photos and videos"',
    'site:instagram.com "online store" "Instagram photos and videos"',
    'site:instagram.com "online shopping" "Instagram photos and videos"',
    'site:instagram.com "delivery available" "Instagram photos and videos"',
    'site:instagram.com "shipping available" "Instagram photos and videos"',
    'site:instagram.com "place your order" "Instagram photos and videos"',

    # --- Region-biased (Pakistan / India) ----------------------------------
    'site:instagram.com "COD available" Pakistan shop',
    'site:instagram.com "DM to order" Pakistan',
    'site:instagram.com "cash on delivery" Lahore OR Karachi',
    'site:instagram.com "DM to order" India shop',
    'site:instagram.com "online store" Pakistan',
    'site:instagram.com "online shopping" Pakistan',
    'site:instagram.com "delivery all over Pakistan"',
    'site:instagram.com "online store" India',
    'site:instagram.com "online shopping" India',
    'site:instagram.com "shipping all over India"',
    'site:instagram.com "WhatsApp for order" Pakistan OR India',
    'site:instagram.com "inbox to order" Pakistan OR India',

    # --- The original bare-phrase dorks, kept for breadth ------------------
    # These skew heavily to reels and so contribute few handles, but they
    # occasionally surface a profile the queries above miss.
    'site:instagram.com "COD available"',
    'site:instagram.com "DM to order"',
    'site:instagram.com "WhatsApp to order"',
    'site:instagram.com "cash on delivery" shop',
    'site:instagram.com "free delivery" order',
    'site:instagram.com "order now" shop',
]

# A result must contain AT LEAST ONE of these in its title + snippet to pass
# (case-insensitive, word-boundary matched). Missing all of them = FAIL.
INCLUDE_KEYWORDS = [
    "cod available",
    "cash on delivery",
    "dm to order",
    "whatsapp to order",
    "order now",
    "shop now",
    "nationwide delivery",
    "free delivery",
    "delivery available",
    "shipping available",
    "delivery all over pakistan",
    "all over pakistan",
    "shipping all over india",
    "all over india",
    "online store",
    "online shopping",
    "whatsapp for order",
    "inbox to order",
    "place your order",
    "to place order",
    "pkr",
    "inr",
    "rs",
    "rs.",
]

# If ANY of these are present, the result auto-FAILS regardless of any include
# match (case-insensitive, word-boundary matched).
EXCLUDE_KEYWORDS = [
    "agency",
    "digital marketing",
    "marketing agency",
    "social media manager",
    "influencer marketing",
    "we help brands",
    "grow your business",
    "public figure",
    "fan page",
]

# --- Searlo API -------------------------------------------------------------
SEARLO_BASE_URL = "https://api.searlo.tech/api/v1"
SEARLO_SEARCH_ENDPOINT = "/search/web"   # current recommended endpoint
RESULTS_PER_QUERY = 10                   # API maximum per page is 10
PAGES_PER_QUERY = 1                      # 1 credit per page -- keep an eye on it
REQUEST_TIMEOUT = 30                     # seconds
MAX_RETRIES = 3                          # per request, for rate-limit/5xx
# Searlo rate-limits the whole /search/* group per minute, and the tier is set
# by credits ever purchased -- a 3,000-credit pool is "Micro" = 20 req/min
# ("Free" = 10/min). 4s spacing keeps requests near 15/min and avoids 429s.
# Raise this if you change query volume.
REQUEST_DELAY = 4.0                      # pause between calls (seconds)

# Print the full raw JSON of the first DEBUG_RAW_LIMIT results per query.
# Enable with the environment variable DEBUG_RAW_RESULTS=1. This exists
# because guessing which Searlo field holds the handle already cost two runs --
# when yield is low, look at the real payload instead of assuming.
DEBUG_RAW_LIMIT = 2

# Print a few example URLs for each discard reason, so a thin run explains
# itself instead of just reporting a number.
LOG_DISCARD_SAMPLES = True
DISCARD_SAMPLE_LIMIT = 5

# Optional Google region / interface bias. Left blank so the first broad pass
# is not artificially narrowed -- the COD / DM-to-order phrasing is itself a
# South Asian signal. Set GOOGLE_COUNTRY = "pk" or "in" later if results skew
# too international.
GOOGLE_COUNTRY = ""                      # e.g. "pk", "in", "us"  (max 5 chars)
GOOGLE_LANGUAGE = ""                     # e.g. "en"             (max 5 chars)

# --- Google Sheets ----------------------------------------------------------
PASS_TAB = "Instagram"          # qualified leads
FAIL_TAB = "Instagram-Backup"   # filtered-out leads, parked for review
PLATFORM_LABEL = "Instagram"
SHEET_SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]

# Column order for both tabs. "Business/Profile" holds the profile URL.
SHEET_HEADER = [
    "Name",
    "Business/Profile",
    "Platform",
    "Date Found",
    "Message Sent",
    "Response",
    "Follow-up 1",
    "Follow-up 2",
    "Status",
]

# Instagram path segments that are NOT a username. When one of these is the
# FIRST path segment the URL is one of the site's own routes and carries no
# handle (instagram.com/reel/<id>/, /p/<id>/, /explore/..., /accounts/login/).
# "_u" is special-cased: m.instagram.com/_u/<handle> is the mobile profile
# redirect, so the handle is the SECOND segment there.
RESERVED_PATHS = {
    "_u", "about", "accounts", "api", "badges", "blog", "create", "direct",
    "explore", "help", "internal_test", "inviter", "jobs", "legal", "live",
    "p", "press", "privacy", "reel", "reels", "s", "shop", "stories",
    "support", "tags", "terms", "tv", "web",
}

# Titles that carry no usable name, so we fall back to the @handle.
GENERIC_TITLES = {
    "", "instagram", "login", "welcome", "page not found", "see",
    "instagram photos and videos",
}

# Strips the trailing boilerplate Google appends to Instagram titles:
#   "... • Instagram photos and videos", "... - Instagram", "... on Instagram: ..."
INSTAGRAM_BOILERPLATE = re.compile(
    r"(?:\s*[•·|\-–—>]\s*|\s+on\s+)instagram\b.*$", re.IGNORECASE
)
HANDLE_RE = re.compile(r"^[A-Za-z0-9._]{1,30}$")
# Post/reel titles read '@handle on Instagram: "caption"', so the handle right
# before "on Instagram" is unambiguously the author. Prefer that match over any
# @mention that might appear inside the caption itself.
AUTHOR_HANDLE_RE = re.compile(
    r"@([A-Za-z0-9._]{1,30})\s+on\s+instagram", re.IGNORECASE
)
# Profile titles: 'COD AVAILABLE (@the__fashzone) - Instagram'
PAREN_HANDLE_RE = re.compile(r"\(\s*@([A-Za-z0-9._]{1,30})\s*\)")
# Instagram's post meta description, which Searlo often returns as the snippet:
#   '1,234 likes, 56 comments - shopname (@shopname) on August 12, 2024: "..."'
META_AUTHOR_RE = re.compile(
    r"-\s*[^(]{0,80}\(\s*@([A-Za-z0-9._]{1,30})\s*\)\s+on\s", re.IGNORECASE
)
TRAILING_HANDLE_RE = re.compile(r"\s*\(\s*@?[A-Za-z0-9._]{1,30}\s*\)\s*$")

# The first implementation carried mojibake variants of Instagram's title
# separators. Redefine the patterns with explicit Unicode escapes so normal
# Google titles using "Name (@handle) \u2022 Instagram photos and videos" clean
# correctly while still accepting mojibake from older console output.
INSTAGRAM_BOILERPLATE = re.compile(
    r"(?:\s*(?:[\u2022\u00b7|>\-]|\u2013|\u2014|â€¢|Â·|â€“|â€”)\s*|\s+on\s+)"
    r"instagram\b.*$",
    re.IGNORECASE,
)
CURRENCY_RE = re.compile(
    r"(?<![A-Za-z0-9])(?:pkr|inr|rs/-|rs\.?|\u20b9)\s*\d",
    re.IGNORECASE,
)


# ----------------------------------------------------------------------------
# Searlo search
# ----------------------------------------------------------------------------

def _retry_delay(response, default=5.0):
    """Best-effort delay (seconds) before retrying a rate-limited request."""
    try:
        body = response.json()
        if isinstance(body, dict) and body.get("retryAfter"):
            return float(body["retryAfter"])
    except (ValueError, AttributeError):
        pass
    header = response.headers.get("Retry-After")
    if header:
        try:
            return float(header)
        except ValueError:
            pass
    return default


def _extract_items(data):
    """
    Pull the result list out of a Searlo response. Searlo has used more than
    one envelope shape, so probe several known containers in priority order
    instead of assuming a single "items" key. Returns [] if none hold a list.
    """
    if not isinstance(data, dict):
        return []
    if data.get("success") is False:
        print(f"    ! Searlo reported success=false: {data.get('message', '(no message)')}")
        return []

    # Top-level containers (covers "items", legacy "data" as a list, etc.).
    for key in ("items", "results", "organic", "web", "data"):
        value = data.get(key)
        if isinstance(value, list):
            return value

    # Results nested under a "data" object.
    payload = data.get("data")
    if isinstance(payload, dict):
        for key in ("items", "results", "organic", "web"):
            value = payload.get(key)
            if isinstance(value, list):
                return value

    return []


def _has_next_page(data):
    """True when searchInformation says another page of results exists."""
    info = data.get("searchInformation") if isinstance(data, dict) else None
    return bool(isinstance(info, dict) and info.get("hasNextPage"))


def _log_credits(response):
    """
    Echo the credit headers Searlo returns. Worth watching because this repo
    shares one credit pool with the LinkedIn automation.
    """
    remaining = response.headers.get("X-Credits-Remaining")
    deducted = response.headers.get("X-Credits-Deducted")
    if remaining is not None or deducted is not None:
        print(f"    i Searlo credits: -{deducted or '?'} on this call, "
              f"{remaining or '?'} remaining (shared with LinkedIn)")


def _dump_empty_response(payload, raw_text):
    """
    Called when a 200 OK response yields zero parsed items. Prints enough of
    the real response to reveal its actual shape (or confirm the search truly
    found nothing), so the parser or query can be corrected without guesswork.
    Set the DEBUG environment variable for a longer body dump.
    """
    if isinstance(payload, dict):
        print(f"    i HTTP 200 but parsed 0 items. Top-level keys: {list(payload.keys())}")
        info = payload.get("searchInformation")
        if isinstance(info, dict):
            print(f"    i searchInformation: totalResults={info.get('totalResults')!r}, "
                  f"query={info.get('query')!r}")
        data = payload.get("data")
        if isinstance(data, dict):
            print(f"    i 'data' keys: {list(data.keys())}")
        if payload.get("message"):
            print(f"    i message: {payload.get('message')!r}")
    limit = 2000 if os.environ.get("DEBUG") else 900
    print(f"    i Raw response body (first {limit} chars):")
    print("      " + raw_text[:limit].replace("\n", "\n      "))


def _searlo_request(url, headers, params):
    """
    Perform ONE HTTP call to Searlo with retries.

    Returns (items, has_next_page). Returns ([], False) on any non-retryable
    failure so the caller can move on to the next query.
    """
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            response = requests.get(url, headers=headers, params=params,
                                    timeout=REQUEST_TIMEOUT)
        except requests.RequestException as exc:
            print(f"    ! Network error (attempt {attempt}/{MAX_RETRIES}): {exc}")
            time.sleep(REQUEST_DELAY * attempt)
            continue

        if response.status_code == 200:
            raw_text = response.text
            try:
                payload = response.json()
            except ValueError:
                print("    ! Could not parse Searlo response as JSON.")
                print(f"    ! Raw body (first 900 chars): {raw_text[:900]}")
                return [], False

            _log_credits(response)
            items = _extract_items(payload)
            if not items:
                _dump_empty_response(payload, raw_text)
            return items, _has_next_page(payload)

        # Retryable: rate limit or upstream warming/unavailable.
        if response.status_code in (429, 502, 503):
            delay = _retry_delay(response)
            print(f"    ! HTTP {response.status_code} (attempt {attempt}/{MAX_RETRIES}); "
                  f"retrying in {delay:.0f}s")
            time.sleep(delay)
            continue

        # Non-retryable: surface a clear, actionable message and stop.
        if response.status_code == 401:
            print("    ! HTTP 401 -- invalid or missing SEARLO_API_KEY.")
        elif response.status_code == 402:
            print("    ! HTTP 402 -- Searlo out of credits (INSUFFICIENT_CREDITS).")
        elif response.status_code == 403:
            print("    ! HTTP 403 -- Searlo account disabled/forbidden.")
        else:
            print(f"    ! HTTP {response.status_code}: {response.text[:200]}")
        return [], False

    print(f"    ! Giving up after {MAX_RETRIES} attempts.")
    return [], False


def searlo_search(query, api_key):
    """
    Run one query against Searlo's web-search endpoint and return the list of
    raw result dicts (each typically containing title / link / snippet).

    Fetches up to PAGES_PER_QUERY pages, stopping early when Searlo reports
    there is no next page. Each page costs 1 credit.
    """
    url = SEARLO_BASE_URL + SEARLO_SEARCH_ENDPOINT
    headers = {"x-api-key": api_key}

    collected = []
    for page in range(1, PAGES_PER_QUERY + 1):
        params = {"q": query, "limit": RESULTS_PER_QUERY, "page": page}
        if GOOGLE_COUNTRY:
            params["gl"] = GOOGLE_COUNTRY
        if GOOGLE_LANGUAGE:
            params["hl"] = GOOGLE_LANGUAGE

        items, has_next = _searlo_request(url, headers, params)
        collected.extend(items)

        if not has_next or page == PAGES_PER_QUERY:
            break
        time.sleep(REQUEST_DELAY)

    return collected


def _debug_dump_raw(items):
    """
    Print the full raw JSON of the first few results for one query.

    Enable with the environment variable DEBUG_RAW_RESULTS=1. When the yield is
    unexpectedly low this shows which field actually carries the handle,
    instead of guessing -- guessing has already cost two runs.
    """
    if not os.environ.get("DEBUG_RAW_RESULTS"):
        return
    for item in items[:DEBUG_RAW_LIMIT]:
        print("    ~ RAW ITEM: " + json.dumps(item, ensure_ascii=False)[:1500])


# ----------------------------------------------------------------------------
# URL validation & handle extraction
# ----------------------------------------------------------------------------

def _parse_instagram_url(url):
    """
    Return (host, path_segments) for an Instagram URL, or (None, []) when the
    host is not instagram.com.

    The HOST must be exactly instagram.com or a subdomain of it. A plain
    `"instagram.com" in url` substring test is unsafe -- it also matches
    fakeinstagram.com, instagram.com.example.net and Google redirect wrappers.
    Subdomains seen in live results include www., m. and www-fallback.
    """
    raw = (url or "").strip()
    if not raw:
        return None, []
    try:
        if "://" not in raw:
            raw = "https://" + raw
        parsed = urlparse(raw)
        host = (parsed.hostname or "").lower()
        path = parsed.path
    except ValueError:
        return None, []

    if host != "instagram.com" and not host.endswith(".instagram.com"):
        return None, []
    return host, [segment for segment in path.split("/") if segment]


def is_instagram_url(url):
    """True when the URL's host is instagram.com or a subdomain of it."""
    host, _ = _parse_instagram_url(url)
    return host is not None


def is_valid_handle(handle):
    """True when a string is plausible as an Instagram username."""
    candidate = (handle or "").strip()
    if not HANDLE_RE.fullmatch(candidate):
        return False
    if candidate.lower() in RESERVED_PATHS:
        return False
    return True


def handle_from_path(url):
    """
    Pull the owning account's handle out of a URL path, or return "".

    Instagram puts the account FIRST in the path of every account-owned page,
    so the first segment is the handle unless it is one of the site's own
    reserved routes:

        instagram.com/ana.apparels/                -> ana.apparels  (profile)
        instagram.com/gogo.fashion3/reels/         -> gogo.fashion3 (reels tab)
        instagram.com/ana.apparels/p/Dc3clFKE0Se/  -> ana.apparels  (a post)
        m.instagram.com/_u/zen_echo_599            -> zen_echo_599  (mobile)
        www-fallback.instagram.com/flinsyshop2     -> flinsyshop2

    Reserved-first-segment routes carry no handle at all:
        instagram.com/reel/DZpusYgzRXE/            -> ""  (the common case)
        instagram.com/p/ABC123/                    -> ""
        instagram.com/explore/tags/cod/            -> ""
        instagram.com/accounts/login/              -> ""
    """
    _, segments = _parse_instagram_url(url)
    if not segments:
        return ""

    first = segments[0].lower()
    if first == "_u":                    # mobile profile redirect
        handle = segments[1] if len(segments) > 1 else ""
        return handle.lower() if is_valid_handle(handle) else ""
    if first in RESERVED_PATHS:          # one of the site's own routes
        return ""
    return segments[0].lower() if is_valid_handle(segments[0]) else ""


def handle_from_text(text):
    """
    Pull a handle out of a SERP title or snippet, or return "".

    Patterns are tried most-specific first. A bare '@something' is deliberately
    NOT accepted: inside a caption it is usually an @mention of a DIFFERENT
    account, and attributing a lead to the wrong handle means Musa DMs a
    stranger and the sheet silently fills with dead rows.
    """
    if not text:
        return ""
    for pattern in (AUTHOR_HANDLE_RE, META_AUTHOR_RE, PAREN_HANDLE_RE):
        match = pattern.search(text)
        if match and is_valid_handle(match.group(1)):
            return match.group(1).lower()
    return ""


def extract_handle(url, title="", snippet=""):
    """
    Best-effort recovery of the owning account's handle from any field.

    Returns (handle, source) where source is "path", "text" or "". The URL path
    wins over title/snippet because it is the page's actual owner rather than
    whichever text Google chose to surface.
    """
    handle = handle_from_path(url)
    if handle:
        return handle, "path"

    handle = handle_from_text(title) or handle_from_text(snippet)
    if handle:
        return handle, "text"

    return "", ""


def normalize_url(url):
    """
    Normalize a profile URL for dedup: lowercase, drop the query string and
    fragment, drop any www./m. host prefix and the trailing slash, so cosmetic
    differences (instagram.com/x vs www.instagram.com/x/?hl=en) don't defeat
    dedup.
    """
    u = (url or "").strip().lower()
    u = u.split("?", 1)[0].split("#", 1)[0]
    if "://" not in u:
        u = "https://" + u

    parsed = urlparse(u)
    host = parsed.hostname or ""
    for prefix in ("www.", "m.", "web."):
        if host.startswith(prefix):
            host = host[len(prefix):]
            break
    path = parsed.path.rstrip("/")

    return f"{host}{path}"


# ----------------------------------------------------------------------------
# Result parsing & filtering
# ----------------------------------------------------------------------------

def parse_name_from_title(title, username):
    """
    Best-effort account name from the SERP title.

    Searlo returns no separate name field, and Instagram titles in Google
    usually look like:
        "Jane's Boutique (@janesboutique) • Instagram photos and videos"
        "Acme Store - Instagram"
        "Login • Instagram"

    The plan expects this column to be thinner than LinkedIn's, so we fall
    back in order: display name (+ handle) -> @handle -> URL username.
    """
    original = (title or "").strip()
    cleaned = INSTAGRAM_BOILERPLATE.sub("", original).strip()

    handle = handle_from_text(original)
    handle_label = f"@{handle}" if handle else ""

    # Strip the handle back out of what remains, so a post title like
    # '@shop on Instagram: "..."' yields "@shop" rather than "@shop (@shop)".
    display = TRAILING_HANDLE_RE.sub("", cleaned).strip()
    display = re.sub(r"^@[A-Za-z0-9._]{1,30}\s*", "", display).strip()
    display = re.sub(r"\s*[•·|\-–—]\s*$", "", display).strip()

    if display.lower() not in GENERIC_TITLES:
        return f"{display} ({handle_label})" if handle_label else display
    if handle_label:
        return handle_label
    return username or "Unknown"


def _keyword_present(keyword, haystack):
    """
    Case-insensitive keyword match that respects word boundaries.

    A plain `keyword in haystack` test makes the "rs." keyword match inside
    "orders.", "years." and "hours." -- and since these are shopping queries,
    "orders." alone would wave almost every result through the INCLUDE filter
    and quietly wreck the precision of the "Instagram" tab. Requiring a
    non-alphanumeric character (or the string edge) on both sides of the match
    keeps the keyword lists exactly as written while fixing that.
    """
    needle = keyword.lower()
    if not needle:
        return False

    start = 0
    while True:
        index = haystack.find(needle, start)
        if index == -1:
            return False

        before_ok = index == 0 or not haystack[index - 1].isalnum()
        end = index + len(needle)
        after_ok = end == len(haystack) or not haystack[end].isalnum()
        if before_ok and after_ok:
            return True
        start = index + 1


def passes_filter(text):
    """
    PASS = at least one INCLUDE keyword AND no EXCLUDE keyword, matched over
    the combined title + snippet. Missing every include term, or hitting any
    exclude term, = FAIL.
    """
    lowered = (text or "").lower()
    has_include = (
        any(_keyword_present(kw, lowered) for kw in INCLUDE_KEYWORDS)
        or bool(CURRENCY_RE.search(text or ""))
    )
    has_exclude = any(_keyword_present(kw, lowered) for kw in EXCLUDE_KEYWORDS)
    return has_include and not has_exclude


def make_row(name, url, today):
    """Build one sheet row in the required column order."""
    return [name, url, PLATFORM_LABEL, today, "", "", "", "", ""]


def _note_sample(bucket, text):
    """Keep the first few examples of a discard reason for the run report."""
    if LOG_DISCARD_SAMPLES and len(bucket) < DISCARD_SAMPLE_LIMIT:
        bucket.append(text)


# ----------------------------------------------------------------------------
# Google Sheets helpers
# ----------------------------------------------------------------------------

def connect_spreadsheet():
    """Authorize with the service account and open the target spreadsheet."""
    creds_json = os.environ.get("GOOGLE_CREDS_JSON")
    sheet_id = os.environ.get("SHEET_ID")
    if not creds_json or not sheet_id:
        sys.exit("ERROR: GOOGLE_CREDS_JSON and SHEET_ID environment variables are required.")

    try:
        creds_info = json.loads(creds_json)
    except json.JSONDecodeError as exc:
        sys.exit(f"ERROR: GOOGLE_CREDS_JSON is not valid JSON: {exc}")

    credentials = Credentials.from_service_account_info(creds_info, scopes=SHEET_SCOPES)
    client = gspread.authorize(credentials)
    return client.open_by_key(sheet_id)


def get_or_create_worksheet(spreadsheet, title):
    """Return the tab, creating it (with a header row) if it doesn't exist."""
    try:
        return spreadsheet.worksheet(title)
    except WorksheetNotFound:
        print(f"  + Tab '{title}' not found -- creating it with a header row.")
        worksheet = spreadsheet.add_worksheet(title=title, rows=200,
                                              cols=len(SHEET_HEADER))
        worksheet.append_row(SHEET_HEADER)
        return worksheet


def ensure_header(worksheet):
    """Write the header row if the tab is currently empty."""
    values = worksheet.get_all_values()
    if not values or not any(str(cell).strip() for cell in values[0]):
        worksheet.append_row(SHEET_HEADER)


def existing_profile_urls(worksheet):
    """
    Set of normalized profile URLs already in THIS tab (column B), skipping
    the header row. Each tab is deduped against its own rows only -- the
    Email / LinkedIn / LinkedIn-Backup tabs are never opened.
    """
    values = worksheet.get_all_values()
    urls = set()
    for row in values[1:]:  # skip header
        if len(row) >= 2:
            normalized = normalize_url(str(row[1]))
            if normalized:
                urls.add(normalized)
    return urls


def append_rows(worksheet, rows):
    """Append rows (if any) and return how many were written."""
    if not rows:
        return 0
    worksheet.append_rows(rows, value_input_option="RAW")
    return len(rows)


# ----------------------------------------------------------------------------
# Main
# ----------------------------------------------------------------------------

def main():
    print("=" * 70)
    print("Instagram Lead-Generation Automation")
    print("=" * 70)

    api_key = os.environ.get("SEARLO_API_KEY")
    if not api_key:
        sys.exit("ERROR: SEARLO_API_KEY environment variable is required.")

    # 1) Run every query in sequence and collect the raw results.
    raw_items = []
    for index, query in enumerate(SEARCH_QUERIES, start=1):
        print(f"\n[{index}/{len(SEARCH_QUERIES)}] Searching: {query}")
        items = searlo_search(query, api_key)
        print(f"    -> {len(items)} raw result(s)")
        _debug_dump_raw(items)
        raw_items.extend(items)
        time.sleep(REQUEST_DELAY)

    print("\n" + "-" * 70)
    print(f"Total queries run        : {len(SEARCH_QUERIES)}")
    print(f"Total raw results fetched: {len(raw_items)}")

    # 2) Resolve every result to an account handle, classify, then dedupe.
    today = date.today().strftime("%Y-%m-%d")
    candidates = {}   # normalized url -> [passed_bool, sheet_row]
    discarded_off_domain = 0
    discarded_no_handle = 0
    from_url_path = 0
    from_title_or_snippet = 0
    intra_run_duplicates = 0
    upgraded_to_pass = 0
    off_domain_samples = []
    no_handle_samples = []

    for item in raw_items:
        url = str(item.get("link") or item.get("url") or "").strip()
        title = str(item.get("title") or "").strip()
        snippet = str(item.get("snippet") or item.get("description") or "").strip()

        if not is_instagram_url(url):
            discarded_off_domain += 1
            _note_sample(off_domain_samples, url)
            continue

        # The handle may live in the URL path, the title, or the snippet. Reel
        # URLs (instagram.com/reel/<id>/) carry it in none of the path segments,
        # which is exactly why the title/snippet fallbacks exist.
        handle, source = extract_handle(url, title, snippet)
        if not handle:
            discarded_no_handle += 1
            _note_sample(no_handle_samples,
                         f"{url}\n        title={title[:70]!r}")
            continue

        if source == "path":
            from_url_path += 1
        else:
            from_title_or_snippet += 1

        # Canonicalize, so instagram.com/x/, m.instagram.com/_u/x,
        # www-fallback.instagram.com/x, instagram.com/x/reels/ and
        # instagram.com/x/p/<id>/ all collapse to ONE dedupable lead.
        handle = handle.lower()
        url = f"https://www.instagram.com/{handle}/"

        normalized = normalize_url(url)
        name = parse_name_from_title(title, handle)
        passed = passes_filter(f"{title} {snippet}")

        seen = candidates.get(normalized)
        if seen is None:
            candidates[normalized] = [passed, make_row(name, url, today)]
            continue

        intra_run_duplicates += 1
        # Same profile surfaced again. A PASS outranks a FAIL: one snippet
        # matching an include keyword is enough to qualify the profile, so
        # don't let a thin snippet from another query bury a real lead.
        if passed and not seen[0]:
            seen[0] = True
            seen[1] = make_row(name, url, today)
            upgraded_to_pass += 1

    pass_rows = [row for passed, row in candidates.values() if passed]
    fail_rows = [row for passed, row in candidates.values() if not passed]

    print(f"Discarded (not instagram.com)          : {discarded_off_domain}")
    print(f"Handles found in the URL path          : {from_url_path}")
    print(f"Handles found in title/snippet         : {from_title_or_snippet}")
    print(f"Discarded (no handle anywhere)         : {discarded_no_handle}")
    print(f"Duplicate URLs within this run         : {intra_run_duplicates}")
    if upgraded_to_pass:
        print(f"Rescued to PASS on a later hit         : {upgraded_to_pass}")
    print(f"Unique profiles kept                   : {len(candidates)}")
    print(f"PASS (qualified)   : {len(pass_rows)}")
    print(f"FAIL (backup)      : {len(fail_rows)}")

    if LOG_DISCARD_SAMPLES:
        for label, samples in (("off-domain", off_domain_samples),
                               ("no recoverable handle", no_handle_samples)):
            if samples:
                print(f"\n  Sample of {label} discards "
                      f"(first {DISCARD_SAMPLE_LIMIT}):")
                for sample in samples:
                    print(f"    - {sample}")

    if not candidates:
        print("\nNothing usable came back from Searlo. Check the messages above "
              "(API key, credits, or query wording) and the Sheet was not touched.")
        return

    # 3) Connect to the Sheet and dedup each tab against its OWN rows only.
    print("\n" + "-" * 70)
    spreadsheet = connect_spreadsheet()

    pass_ws = get_or_create_worksheet(spreadsheet, PASS_TAB)
    fail_ws = get_or_create_worksheet(spreadsheet, FAIL_TAB)
    ensure_header(pass_ws)
    ensure_header(fail_ws)

    existing_pass = existing_profile_urls(pass_ws)
    existing_fail = existing_profile_urls(fail_ws)
    print(f"Existing rows in '{PASS_TAB}'     : {len(existing_pass)}")
    print(f"Existing rows in '{FAIL_TAB}': {len(existing_fail)}")

    new_pass = [r for r in pass_rows if normalize_url(r[1]) not in existing_pass]
    new_fail = [r for r in fail_rows if normalize_url(r[1]) not in existing_fail]

    dupes_pass_skipped = len(pass_rows) - len(new_pass)
    dupes_fail_skipped = len(fail_rows) - len(new_fail)

    # 4) Append the new rows to each tab.
    added_pass = append_rows(pass_ws, new_pass)
    added_fail = append_rows(fail_ws, new_fail)

    # 5) Final summary.
    print("\n" + "=" * 70)
    print("SUMMARY")
    print("=" * 70)
    print(f"Queries run                  : {len(SEARCH_QUERIES)}")
    print(f"Raw results fetched          : {len(raw_items)}")
    print(f"Discarded (off-domain)       : {discarded_off_domain}")
    print(f"Handles from URL path        : {from_url_path}")
    print(f"Handles from title/snippet   : {from_title_or_snippet}")
    print(f"Discarded (no handle found)  : {discarded_no_handle}")
    print(f"PASS / FAIL                  : {len(pass_rows)} / {len(fail_rows)}")
    print(f"Tab '{PASS_TAB}':")
    print(f"    duplicates skipped (already in tab): {dupes_pass_skipped}")
    print(f"    new rows added                     : {added_pass}")
    print(f"Tab '{FAIL_TAB}':")
    print(f"    duplicates skipped (already in tab): {dupes_fail_skipped}")
    print(f"    new rows added                     : {added_fail}")
    print("=" * 70)
    print("Done.")


if __name__ == "__main__":
    main()
