"""
hireme_dedupe.py - used by the daily HireMe Claude task to skip jobs the
GitHub bot already sent to Telegram.

Reads the bot's list of sent jobs from the repo (origin/main:state/seen_jobs.json)
without touching any local file, and prints the job_ids from a candidates file
that match a job the bot already sent.

Usage:
    python3 hireme_dedupe.py candidates.json
    candidates.json: [{"job_id": 1, "title": "...", "company": "..."}, ...]
Output (stdout): JSON list of job_ids that were already sent by the bot.
"""

import json
import re
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent
BOT_SEEN_PATH = "origin/main:state/seen_jobs.json"


def normalize(text):
    return re.sub(r"\W", "", (text or "").lower())


COMPANY_SUFFIXES = re.compile(r'בע["״\']?מ|\b(?:ltd|limited|inc|llc|corp)\b\.?|\(il\)', re.I)


def fingerprints(title, company):
    # Same formulas as job_bot.fingerprint() and job_bot.legacy_fingerprint()
    stripped = COMPANY_SUFFIXES.sub("", company or "")
    return {"fp:" + normalize(f"{title}|{stripped}"), "fp:" + normalize(f"{title}|{company}")}


def load_bot_seen():
    try:
        subprocess.run(["git", "-C", str(REPO), "fetch", "-q", "origin"],
                       check=True, capture_output=True, timeout=60)
        out = subprocess.run(["git", "-C", str(REPO), "show", BOT_SEEN_PATH],
                             check=True, capture_output=True, timeout=30)
        return json.loads(out.stdout.decode("utf-8"))
    except (subprocess.SubprocessError, json.JSONDecodeError) as e:
        print(f"warning: could not read the bot's seen list ({e}); skipping dedupe",
              file=sys.stderr)
        return {}


def main():
    candidates = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    seen = load_bot_seen()

    seen_fps = {k for k in seen if k.startswith("fp:")}
    # Looser match for company names written differently on each site
    # ("Applied Materials Israel" vs "Applied Materials - Israel"):
    # same normalized title + first word of the company.
    fps_by_title = {}
    for key, record in seen.items():
        if key.startswith("fp:") and isinstance(record, dict):
            fps_by_title.setdefault(normalize(record.get("title")), []).append(key)

    already_sent = []
    for job in candidates:
        title, company = job.get("title", ""), job.get("company", "")
        if fingerprints(title, company) & seen_fps:
            already_sent.append(job["job_id"])
            continue
        first_word = normalize((company or "").split(" ")[0]) if company else ""
        if first_word and any(first_word in fp for fp in fps_by_title.get(normalize(title), [])):
            already_sent.append(job["job_id"])

    print(json.dumps(already_sent))


if __name__ == "__main__":
    main()
