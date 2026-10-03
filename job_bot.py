"""
job_bot.py - finds new student/junior jobs in Israel and sends a daily
digest to Telegram.

Sources:
  - LinkedIn public job search (main source, no API key, no quota)
  - Google search via SerpApi (optional, off by default - see USE_GOOGLE_SEARCH)

Usage:
    python job_bot.py              # search, send new jobs, update seen_jobs.json
    python job_bot.py --dry-run    # search and print only (no Telegram, no save)
    python job_bot.py --test       # send a test message to Telegram
    python job_bot.py --get-chat-id  # print chat IDs that messaged your bot
"""

import argparse
import html
import json
import os
import re
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import requests
from dotenv import load_dotenv

# ============================================================
# Search settings - edit these
# ============================================================

# ---- LinkedIn (main source) ----
# Each keyword is searched in LinkedIn's public job listings for Israel.
# Free and unlimited, so it can be a longer list.
LINKEDIN_KEYWORDS = [
    "Student Data Analyst",
    "Junior Data Analyst",
    "Data Analyst Student",
    "BI Student",
    "Data Engineer Student",
    "Junior Data Engineer",
    "Industrial Engineering Student",
    "סטודנט תעשייה וניהול",
    "PMO Student",
    "Project Student",
    "Junior Project Manager",
    "Operations Student",
    "Information Systems Student",
    "סטודנט מערכות מידע",
    "סטודנט דאטה",
]

# Only jobs posted in the last N seconds. 3 days covers days the computer
# was off; seen_jobs.json prevents getting the same job twice.
LINKEDIN_POSTED_WITHIN = 3 * 24 * 3600

# Result pages per keyword (10 jobs per page).
LINKEDIN_PAGES = 2

# ---- Google via SerpApi (optional) ----
# Off by default: Google returns very few job pages from the last 24h and
# each search takes ~80s. Set to True to also run SEARCH_QUERIES.
USE_GOOGLE_SEARCH = False

# Each query = 1 SerpApi search per run (free plan: 250 searches/month).
# 4 queries x 31 days = 124 searches, leaving plenty for manual tests.
# Results are limited to pages Google indexed in the last 24 hours (SEARCH_TIME).
SEARCH_QUERIES = [
    'site:il.linkedin.com/jobs/view/ ("student" OR "junior" OR "סטודנט") ("data analyst" OR "industrial engineering" OR "pmo")',
    'site:il.linkedin.com/jobs/view/ ("student" OR "junior") ("operations" OR "information systems" OR "project")',
    'site:drushim.co.il/job/ ("סטודנט" OR "ג\'וניור") ("תעשייה וניהול" OR "אנליסט" OR "מערכות מידע")',
    'site:alljobs.co.il/job/ ("סטודנט" OR "Junior") ("תעשייה וניהול" OR "Data Analyst" OR "PMO")',
]

# Google "tbs" time filter: qdr:d = last 24h, qdr:w = last week.
SEARCH_TIME = "qdr:d"

# Keyword matching (case-insensitive):
# - English keywords match whole words only, so "bi" won't match "mobile"
#   and "vp" won't match inside other words. List plurals explicitly.
# - Hebrew keywords match anywhere in the title, so prefixes like ו/ה/ב/ל
#   ("והנדסה", "בפרויקט") still match. "מחלק" covers both מחלקה and מחלקת.

# A result is kept only if it matches BOTH groups:
#   at least one ROLE keyword (checked in the title)
#   AND at least one LEVEL keyword (checked in the title or snippet).

# Group A - professional field
ROLE_KEYWORDS = [
    "data", "נתונים", "דאטה", "analyst", "אנליסט", "אנליסטית", "bi",
    "industrial", "תעשייה וניהול", "תעשיה וניהול",
    "pmo", "project", "projects", "פרויקט", "פרויקטים",
    "information systems", "מערכות מידע", "operations", "אופרציה",
    # Hebrew/English equivalents and core תעשייה וניהול fields
    "תפעול", "business intelligence", "supply chain", "שרשרת אספקה",
    "logistics", "לוגיסטיקה", "procurement", "purchasing", "רכש",
]

# Group B - experience level
LEVEL_KEYWORDS = [
    "student", "students", "סטודנט", "סטודנטית", "junior", "ג'וניור", "גוניור",
    "intern", "internship", "התמחות", "מתמחה", "graduate", "בוגר",
    "entry", "entry-level",
]

# Drop a result if its title contains any of these.
# Note: "manager" alone is intentionally NOT here, so roles like
# "Project Manager" / "PMO Project Manager" are kept.
EXCLUDE_KEYWORDS = [
    # seniority
    "senior", "sr", "lead", "leader", "team lead", "principal", "staff",
    "architect", "director", "head of", "vp", "vice president", "chief",
    "cto", "cio", "coo", "general manager", "group manager",
    "engineering manager", "senior manager",
    "בכיר", "בכירה", "ראש צוות", "ראש תחום", "ראש מחלק", "מנהל מחלק",
    "מנהלת מחלק", "מנהל/ת מחלק", "סמנכ\"ל", "סמנכ״ל", "סמנכל",
    # production planning (תפ"י)
    "planner", "planning and control", "production planning",
    "פלנר", "פלנרית", "תפי", "תפ\"י", "תפ״י", "תכנון ופיקוח",
    "תכנון ובקרת ייצור", "תכנון ובקרה",
    # customer service / sales / support
    "שירות לקוחות", "נציג", "נציגת", "נציג/ת", "מוקד", "טלמרקטינג",
    "מכירות", "sales", "customer service", "help desk", "תמיכה טכנית",
    # "Data Entry" would otherwise pass via "data" + "entry"
    "data entry", "הקלדה", "הקלדת נתונים",
]

# STRICT whitelist: a result is kept only if its URL (without "https://"
# and "www.") starts with one of these job-page prefixes. Everything else
# (Instagram, Facebook, home pages, blogs, other LinkedIn countries) is dropped.
ALLOWED_URL_PREFIXES = [
    "linkedin.com/jobs/view/",
    "il.linkedin.com/jobs/view/",
    "drushim.co.il/job/",
    "alljobs.co.il/job/",
    "alljobs.co.il/search/uploadsingle.aspx",  # AllJobs single-job pages
    "jobmaster.co.il",
]

# Drop a result if its title or URL mentions any of these (outside Israel).
# English entries match whole words only.
FOREIGN_LOCATIONS = [
    "singapore", "luxembourg", "calgary", "canada", "usa", "uk",
    "united states", "germany", "france", "australia", "remote worldwide",
    "india", "london",
    # extra coverage
    "united kingdom", "england", "toronto", "vancouver", "berlin", "munich",
    "paris", "netherlands", "amsterdam", "spain", "poland", "ireland",
    "dublin", "new york", "san francisco", "california", "texas",
    "bangalore", "bengaluru", "hyderabad", "remote us", "bangkok", "dubai",
    "ארה\"ב", "ארה״ב", "ארצות הברית", "בריטניה", "לונדון", "קנדה",
    "גרמניה", "הודו", "צרפת", "סינגפור",
]

# Drop a result if its title contains any of these (non-job pages).
JUNK_TITLE_KEYWORDS = ["דף הבית", "archives", "קרן", "סקר", "פורום", "קבוצה"]

# Forget jobs in seen_jobs.json after this many days (keeps the file small).
SEEN_RETENTION_DAYS = 90

# ============================================================

BASE_DIR = Path(__file__).resolve().parent
SEEN_FILE = BASE_DIR / "seen_jobs.json"
SERPAPI_URL = "https://serpapi.com/search.json"
TELEGRAM_MAX_LEN = 4000  # Telegram limit is 4096; keep some margin

load_dotenv(BASE_DIR / ".env")


def env(name):
    value = os.getenv(name, "").strip()
    if not value:
        sys.exit(f"Missing {name} in .env (see .env.example)")
    return value


# ---------- Fetching ----------

# Query-string params that only track clicks; dropped so the same job
# always maps to the same URL. Other params (e.g. AllJobs' JobID) are kept.
TRACKING_PARAMS = {"trk", "trackingid", "refid", "position", "pagenum",
                   "originalsubdomain", "src", "ref", "fbclid", "gclid"}

# Suffixes Google appends to page titles, e.g. "... | LinkedIn"
TITLE_SUFFIX = re.compile(r"\s*[|\-–]\s*(linkedin|drushim|דרושים|alljobs|אולג'ובס)[^|\-–]*$", re.I)


LINKEDIN_URL = "https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search"
LINKEDIN_HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                   "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"),
}


def _card_field(card, pattern):
    match = re.search(pattern, card, re.S)
    if not match:
        return ""
    text = re.sub(r"<[^>]+>", "", match.group(1))
    return html.unescape(" ".join(text.split()))


def parse_linkedin_cards(page_html):
    jobs = []
    for card in page_html.split("<li")[1:]:
        link = re.search(r'class="base-card__full-link[^"]*"\s+href="([^"]+)"', card)
        if not link:
            continue
        jobs.append({
            "title": _card_field(card, r'base-search-card__title">(.*?)</h3>'),
            "company": _card_field(card, r'base-search-card__subtitle">(.*?)</h4>'),
            "location": _card_field(card, r'job-search-card__location">(.*?)</span>'),
            "date": _card_field(card, r'<time[^>]*datetime="([^"]+)"'),
            "link": clean_url(html.unescape(link.group(1))),
            "snippet": "",
            "source": "LinkedIn",
        })
    return jobs


def fetch_linkedin(keyword):
    jobs = []
    for page in range(LINKEDIN_PAGES):
        params = {
            "keywords": keyword,
            "location": "Israel",
            "f_TPR": f"r{LINKEDIN_POSTED_WITHIN}",
            "start": page * 10,
        }
        resp = requests.get(LINKEDIN_URL, params=params,
                            headers=LINKEDIN_HEADERS, timeout=30)
        if resp.status_code == 429:
            print("  LinkedIn rate limit - waiting 30s")
            time.sleep(30)
            resp = requests.get(LINKEDIN_URL, params=params,
                                headers=LINKEDIN_HEADERS, timeout=30)
        resp.raise_for_status()
        cards = parse_linkedin_cards(resp.text)
        jobs.extend(cards)
        time.sleep(1.5)
        if len(cards) < 10:
            break
    return jobs


def fetch_results(query, api_key):
    params = {
        "engine": "google",
        "q": query,
        "location": "Israel",
        "google_domain": "google.co.il",
        "gl": "il",
        "hl": "iw",
        "tbs": SEARCH_TIME,
        "api_key": api_key,
    }
    resp = requests.get(SERPAPI_URL, params=params, timeout=(10, 150))
    data = resp.json()
    if "error" in data:
        # "Google hasn't returned any results" is normal with a 24h filter
        print(f"  {data['error']}")
        return []
    resp.raise_for_status()
    return data.get("organic_results", [])


def clean_url(url):
    parts = urlsplit(url)
    query = [(k, v) for k, v in parse_qsl(parts.query)
             if k.lower() not in TRACKING_PARAMS and not k.lower().startswith("utm_")]
    path = parts.path.rstrip("/") or "/"
    return urlunsplit((parts.scheme, parts.netloc.lower(), path, urlencode(query), ""))


def source_name(result, url):
    domain = urlsplit(url).netloc.lower()
    if domain.startswith("www."):
        domain = domain[4:]
    return result.get("source") or domain


def normalize(result):
    url = clean_url(result.get("link", ""))
    return {
        "title": TITLE_SUFFIX.sub("", result.get("title", "")).strip(),
        "snippet": result.get("snippet", "").strip(),
        "source": source_name(result, url),
        "company": "",
        "location": "",
        "link": url,
        "date": result.get("date", ""),
    }


def _keyword_pattern(keyword):
    """Whole-word regex for Latin keywords, plain substring for Hebrew."""
    escaped = re.escape(keyword.lower())
    if re.search(r"[a-z]", keyword.lower()):
        return re.compile(rf"(?<![a-z0-9]){escaped}(?![a-z0-9])")
    return re.compile(escaped)


FOREIGN_PATTERNS = [_keyword_pattern(k) for k in FOREIGN_LOCATIONS]
JUNK_PATTERNS = [_keyword_pattern(k) for k in JUNK_TITLE_KEYWORDS]
ROLE_PATTERNS = [_keyword_pattern(k) for k in ROLE_KEYWORDS]
LEVEL_PATTERNS = [_keyword_pattern(k) for k in LEVEL_KEYWORDS]
EXCLUDE_PATTERNS = [_keyword_pattern(k) for k in EXCLUDE_KEYWORDS]


def is_allowed_url(url):
    parts = urlsplit(url.lower())
    host = parts.netloc[4:] if parts.netloc.startswith("www.") else parts.netloc
    path = f"{host}{parts.path}"
    if parts.query:
        path += f"?{parts.query}"
    return any(path.startswith(prefix) for prefix in ALLOWED_URL_PREFIXES)


def matches_filters(job):
    title = job["title"].lower()
    text = f"{title} {job['snippet'].lower()}"
    title_and_url = f"{title} {job['link'].lower()}"
    if not is_allowed_url(job["link"]):
        return False
    if any(p.search(title_and_url) for p in FOREIGN_PATTERNS):
        return False
    if any(p.search(title) for p in JUNK_PATTERNS):
        return False
    if any(p.search(title) for p in EXCLUDE_PATTERNS):
        return False
    if not any(p.search(title) for p in ROLE_PATTERNS):
        return False
    if not any(p.search(text) for p in LEVEL_PATTERNS):
        return False
    return True


def _add_jobs(jobs, candidates):
    kept = 0
    for job in candidates:
        if job["link"] and job["link"] not in jobs and matches_filters(job):
            jobs[job["link"]] = job
            kept += 1
    print(f"  {len(candidates)} results, {kept} kept")


def collect_jobs(api_key):
    jobs = {}
    for keyword in LINKEDIN_KEYWORDS:
        print(f"LinkedIn: {keyword}")
        try:
            _add_jobs(jobs, fetch_linkedin(keyword))
        except requests.RequestException as e:
            print(f"  request failed: {e}")

    if USE_GOOGLE_SEARCH:
        for query in SEARCH_QUERIES:
            print(f"Google: {query}")
            try:
                _add_jobs(jobs, [normalize(r) for r in fetch_results(query, api_key)])
            except requests.RequestException as e:
                print(f"  request failed: {e}")
            time.sleep(1)
    return list(jobs.values())


# ---------- Seen jobs ----------

def load_seen():
    if not SEEN_FILE.exists():
        return {}
    try:
        return json.loads(SEEN_FILE.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        print("seen_jobs.json is corrupted - starting fresh")
        return {}


def save_seen(seen):
    cutoff = (datetime.now() - timedelta(days=SEEN_RETENTION_DAYS)).isoformat()
    seen = {k: v for k, v in seen.items() if v.get("seen_at", "") >= cutoff}
    tmp = SEEN_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(seen, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(SEEN_FILE)


# ---------- Telegram ----------

def send_telegram(text, token, chat_id):
    resp = requests.post(
        f"https://api.telegram.org/bot{token}/sendMessage",
        json={
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "HTML",
            "link_preview_options": {"is_disabled": True},
        },
        timeout=30,
    )
    if not resp.ok:
        raise RuntimeError(f"Telegram error {resp.status_code}: {resp.text}")


def format_job(i, job):
    e = html.escape
    lines = [f"<b>{i}. {e(job['title'] or 'ללא כותרת')}</b>"]
    if job["company"]:
        lines.append(f"🏢 {e(job['company'])}")
    if job["location"]:
        lines.append(f"📍 {e(job['location'])}")
    source = job["source"] + (f" · {job['date']}" if job["date"] else "")
    lines.append(f"🌐 {e(source)}")
    lines.append(f'🔗 <a href="{e(job["link"], quote=True)}">לצפייה והגשה</a>')
    return "\n".join(lines)


def build_messages(jobs):
    """Split the digest into chunks that fit Telegram's message limit."""
    header = f"📋 <b>{len(jobs)} משרות חדשות</b> · {datetime.now():%d/%m/%Y}\n\n"
    messages, current = [], header
    for i, job in enumerate(jobs, 1):
        block = format_job(i, job) + "\n\n"
        if len(current) + len(block) > TELEGRAM_MAX_LEN:
            messages.append(current)
            current = ""
        current += block
    if current.strip():
        messages.append(current)
    return messages


def print_chat_ids(token):
    resp = requests.get(f"https://api.telegram.org/bot{token}/getUpdates", timeout=30)
    updates = resp.json().get("result", [])
    if not updates:
        print("No messages found. Send any message to your bot in Telegram, then retry.")
        return
    chats = {}
    for u in updates:
        msg = u.get("message") or u.get("channel_post") or {}
        chat = msg.get("chat")
        if chat:
            chats[chat["id"]] = chat.get("username") or chat.get("title") or chat.get("first_name")
    for chat_id, name in chats.items():
        print(f"Chat ID: {chat_id}  ({name})")


# ---------- Main ----------

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="print only, don't send or save")
    parser.add_argument("--test", action="store_true", help="send a test Telegram message")
    parser.add_argument("--get-chat-id", action="store_true", help="show your Telegram chat ID")
    args = parser.parse_args()

    if args.get_chat_id:
        print_chat_ids(env("TELEGRAM_BOT_TOKEN"))
        return

    if args.test:
        send_telegram("✅ הבוט מחובר! סיכום המשרות היומי יגיע לכאן.",
                      env("TELEGRAM_BOT_TOKEN"), env("TELEGRAM_CHAT_ID"))
        print("Test message sent.")
        return

    api_key = env("SERPAPI_KEY") if USE_GOOGLE_SEARCH else None
    if not args.dry_run:
        token, chat_id = env("TELEGRAM_BOT_TOKEN"), env("TELEGRAM_CHAT_ID")

    seen = load_seen()
    jobs = collect_jobs(api_key)
    new_jobs = [j for j in jobs if j["link"] not in seen]
    print(f"Found {len(jobs)} matching jobs, {len(new_jobs)} new.")

    if args.dry_run:
        for i, job in enumerate(new_jobs, 1):
            where = " | ".join(x for x in (job["company"], job["location"]) if x)
            print(f"{i}. {job['title']} | {where or job['source']}\n   {job['link']}")
        return

    if not new_jobs:
        print("Nothing new - no message sent.")
        return

    for message in build_messages(new_jobs):
        send_telegram(message, token, chat_id)
        time.sleep(1)

    # Mark as seen only after a successful send
    now = datetime.now().isoformat()
    for job in new_jobs:
        seen[job["link"]] = {"title": job["title"], "source": job["source"],
                             "seen_at": now}
    save_seen(seen)
    print(f"Sent {len(new_jobs)} jobs to Telegram.")


if __name__ == "__main__":
    main()
