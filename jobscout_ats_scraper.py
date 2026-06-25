"""jobscout_ats_scraper.py — Direct ATS scraper for Greenhouse and Lever employers.

Fetches all open job postings from employers in config/target_employers.json
that use Greenhouse or Lever, writing output compatible with the existing
Job Scout scoring pipeline.

Outputs (both written to --run-dir):
  scan_ats.jsonl   minimal records for seen-ID tracking (picked up by jobscout_seen.py)
  raw_ats.jsonl    full records with descriptions (appended into raw_desc.jsonl before Step 8)

Usage:
  python3 jobscout_ats_scraper.py \\
      --employers config/target_employers.json \\
      --seen      job-scout-seen.json \\
      --run-dir   run
"""

import argparse
import json
import re
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

GREENHOUSE_API = "https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=true"
LEVER_API = "https://api.lever.co/v0/postings/{slug}"
ASHBY_API = "https://api.ashbyhq.com/posting-api/job-board/{slug}"

USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
REQUEST_TIMEOUT = 15
INTER_REQUEST_DELAY = 0.15  # seconds between employer fetches


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _fetch_json(url: str):
    """Fetch URL, return parsed JSON. Raises urllib.error.HTTPError on failure."""
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT) as r:
        return json.loads(r.read())


def _strip_html(html: str) -> str:
    """Strip HTML tags and collapse whitespace."""
    if not html:
        return ""
    text = re.sub(r"<[^>]+>", " ", html)
    text = re.sub(r"&nbsp;", " ", text)
    text = re.sub(r"&amp;", "&", text)
    text = re.sub(r"&lt;", "<", text)
    text = re.sub(r"&gt;", ">", text)
    text = re.sub(r"&quot;", '"', text)
    text = re.sub(r"&#39;", "'", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _load_seen_ids(seen_path: Path) -> set:
    if not seen_path.exists():
        return set()
    try:
        data = json.loads(seen_path.read_text())
        if isinstance(data, list):
            return set(str(x) for x in data)
        ids = data.get("ids", [])
        return set(str(x) for x in ids)
    except Exception:
        return set()


def _write_jsonl(path: Path, records: list) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for rec in records:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")


# ---------------------------------------------------------------------------
# Greenhouse
# ---------------------------------------------------------------------------

def _cutoff_date(max_age_days: int) -> str:
    """Return ISO date string for the oldest acceptable posting date."""
    return (datetime.now(tz=timezone.utc) - timedelta(days=max_age_days)).date().isoformat()


def fetch_greenhouse(slug: str, employer_name: str, cutoff: str = "") -> list:
    """Fetch jobs from a Greenhouse board posted on or after cutoff (YYYY-MM-DD)."""
    url = GREENHOUSE_API.format(slug=slug)
    try:
        data = _fetch_json(url)
    except urllib.error.HTTPError as e:
        if e.code == 404:
            print(f"  [gh] {employer_name} ({slug}): board not found (404) — skipping", file=sys.stderr)
            return []
        raise
    except Exception as e:
        print(f"  [gh] {employer_name} ({slug}): fetch error — {e}", file=sys.stderr)
        return []

    jobs = data.get("jobs", [])
    out = []
    now_iso = datetime.now(tz=timezone.utc).isoformat()
    for j in jobs:
        loc = (j.get("location") or {}).get("name") or ""
        # Prefer first_published over updated_at for posted date
        posted_raw = j.get("first_published") or j.get("updated_at") or ""
        posted = posted_raw[:10] if posted_raw else ""
        if cutoff and posted and posted < cutoff:
            continue
        description = _strip_html(j.get("content") or "")
        loc_lower = loc.lower()
        if "remote" in loc_lower:
            workplace = "Remote"
        elif "hybrid" in loc_lower:
            workplace = "Hybrid"
        else:
            workplace = ""
        out.append({
            "id": f"gh_{j['id']}",
            "title": j.get("title", ""),
            "companyName": employer_name,
            "company": employer_name,
            "location": loc,
            "postedAt": posted,
            "posted": posted,
            "link": j.get("absolute_url", ""),
            "source": "greenhouse",
            "_src": "ats",
            "addedAt": now_iso,
            "workplace": workplace,
            "description": description,
        })
    return out


# ---------------------------------------------------------------------------
# Lever
# ---------------------------------------------------------------------------

def fetch_lever(slug: str, employer_name: str, cutoff: str = "") -> list:
    """Fetch postings from a Lever board posted on or after cutoff (YYYY-MM-DD)."""
    url = LEVER_API.format(slug=slug)
    try:
        postings = _fetch_json(url)
    except urllib.error.HTTPError as e:
        if e.code == 404:
            print(f"  [lv] {employer_name} ({slug}): board not found (404) — skipping", file=sys.stderr)
            return []
        raise
    except Exception as e:
        print(f"  [lv] {employer_name} ({slug}): fetch error — {e}", file=sys.stderr)
        return []

    if not isinstance(postings, list):
        print(f"  [lv] {employer_name} ({slug}): unexpected response format", file=sys.stderr)
        return []

    out = []
    now_iso = datetime.now(tz=timezone.utc).isoformat()
    for p in postings:
        cats = p.get("categories") or {}
        loc = cats.get("location") or cats.get("allLocations", [""])[0] if cats.get("allLocations") else ""
        ts = p.get("createdAt") or 0
        try:
            posted = datetime.fromtimestamp(int(ts) / 1000, tz=timezone.utc).date().isoformat() if ts else ""
        except Exception:
            posted = ""
        if cutoff and posted and posted < cutoff:
            continue

        # Combine description sections; prefer descriptionPlain for cleaner text
        desc_html = (p.get("description") or "") + (p.get("additional") or "")
        desc_plain = (p.get("descriptionPlain") or "") + (p.get("additionalPlain") or "")
        description = desc_plain.strip() if desc_plain.strip() else _strip_html(desc_html)

        # Lever workplaceType: "remote" | "hybrid" | "onsite" — map to jobscout_core conventions
        wt = (p.get("workplaceType") or "").lower()
        workplace = {"remote": "Remote", "hybrid": "Hybrid", "onsite": "On-site"}.get(wt, "")

        out.append({
            "id": f"lv_{p['id']}",
            "title": p.get("text", ""),
            "companyName": employer_name,
            "company": employer_name,
            "location": str(loc),
            "postedAt": posted,
            "posted": posted,
            "link": p.get("hostedUrl", ""),
            "source": "lever",
            "_src": "ats",
            "addedAt": now_iso,
            "workplace": workplace,
            "description": description,
        })
    return out


# ---------------------------------------------------------------------------
# Ashby
# ---------------------------------------------------------------------------

def fetch_ashby(slug: str, employer_name: str, cutoff: str = "") -> list:
    """Fetch jobs from an Ashby board posted on or after cutoff (YYYY-MM-DD)."""
    url = ASHBY_API.format(slug=slug)
    try:
        data = _fetch_json(url)
    except urllib.error.HTTPError as e:
        if e.code == 404:
            print(f"  [ash] {employer_name} ({slug}): board not found (404) — skipping", file=sys.stderr)
            return []
        raise
    except Exception as e:
        print(f"  [ash] {employer_name} ({slug}): fetch error — {e}", file=sys.stderr)
        return []

    jobs = data.get("jobs", [])
    out = []
    now_iso = datetime.now(tz=timezone.utc).isoformat()
    for j in jobs:
        posted_raw = j.get("publishedAt") or ""
        posted = posted_raw[:10] if posted_raw else ""
        if cutoff and posted and posted < cutoff:
            continue

        description = (j.get("descriptionPlain") or "").strip() or _strip_html(j.get("descriptionHtml") or "")

        wt = j.get("workplaceType") or ""
        if wt == "Remote":
            workplace = "Remote"
        elif wt == "Hybrid":
            workplace = "Hybrid"
        elif wt == "OnSite":
            workplace = "On-site"
        elif j.get("isRemote"):
            workplace = "Remote"
        else:
            workplace = ""

        out.append({
            "id": f"ash_{j['id']}",
            "title": j.get("title", ""),
            "companyName": employer_name,
            "company": employer_name,
            "location": j.get("location") or "",
            "postedAt": posted,
            "posted": posted,
            "link": j.get("jobUrl", ""),
            "source": "ashby",
            "_src": "ats",
            "addedAt": now_iso,
            "workplace": workplace,
            "description": description,
        })
    return out


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Scrape Greenhouse and Lever ATS job boards for target employers."
    )
    parser.add_argument("--employers", default="config/target_employers.json",
                        help="Path to target_employers.json")
    parser.add_argument("--seen", default="job-scout-seen.json",
                        help="Path to job-scout-seen.json for deduplication")
    parser.add_argument("--run-dir", default="run",
                        help="Output directory (default: run)")
    parser.add_argument("--max-age-days", type=int, default=7,
                        help="Only include jobs posted within this many days (default: 7, 0 = no filter)")
    parser.add_argument("--max-age-hours", type=int, default=0,
                        help="Lookback window in hours; overrides --max-age-days when > 0")
    args = parser.parse_args()

    employers_path = Path(args.employers)
    seen_path = Path(args.seen)
    run_dir = Path(args.run_dir)

    # Load employer list
    try:
        all_employers = json.loads(employers_path.read_text()).get("employers", [])
    except Exception as e:
        print(f"[ats] ERROR: cannot load {employers_path}: {e}", file=sys.stderr)
        sys.exit(1)

    gh_employers = [e for e in all_employers if e.get("ats") == "Greenhouse" and e.get("atsSlug")]
    lv_employers = [e for e in all_employers if e.get("ats") == "Lever" and e.get("atsSlug")]
    ash_employers = [e for e in all_employers if e.get("ats") == "Ashby" and e.get("atsSlug")]
    if args.max_age_hours > 0:
        cutoff = (datetime.now(tz=timezone.utc) - timedelta(hours=args.max_age_hours)).date().isoformat()
    else:
        cutoff = _cutoff_date(args.max_age_days) if args.max_age_days > 0 else ""
    print(f"[ats] Target employers: {len(gh_employers)} Greenhouse, {len(lv_employers)} Lever, {len(ash_employers)} Ashby")
    if cutoff:
        win = f"{args.max_age_hours}h" if args.max_age_hours > 0 else f"{args.max_age_days} days"
        print(f"[ats] Date filter: postings on or after {cutoff} ({win})")

    # Load seen IDs
    seen_ids = _load_seen_ids(seen_path)
    print(f"[ats] Loaded {len(seen_ids)} seen IDs")

    all_jobs = []
    gh_new = gh_skipped = gh_errors = 0
    lv_new = lv_skipped = lv_errors = 0
    ash_new = ash_skipped = ash_errors = 0

    # Fetch Greenhouse
    print(f"[ats] Fetching Greenhouse boards...")
    for emp in gh_employers:
        slug = emp["atsSlug"]
        name = emp["name"]
        try:
            jobs = fetch_greenhouse(slug, name, cutoff=cutoff)
        except Exception as e:
            print(f"  [gh] {name}: unexpected error — {e}", file=sys.stderr)
            gh_errors += 1
            time.sleep(INTER_REQUEST_DELAY)
            continue

        new_jobs = [j for j in jobs if j["id"] not in seen_ids]
        gh_new += len(new_jobs)
        gh_skipped += len(jobs) - len(new_jobs)
        if new_jobs:
            print(f"  [gh] {name}: {len(new_jobs)} new / {len(jobs)} total")
        all_jobs.extend(new_jobs)
        time.sleep(INTER_REQUEST_DELAY)

    # Fetch Lever
    print(f"[ats] Fetching Lever boards...")
    for emp in lv_employers:
        slug = emp["atsSlug"]
        name = emp["name"]
        try:
            jobs = fetch_lever(slug, name, cutoff=cutoff)
        except Exception as e:
            print(f"  [lv] {name}: unexpected error — {e}", file=sys.stderr)
            lv_errors += 1
            time.sleep(INTER_REQUEST_DELAY)
            continue

        new_jobs = [j for j in jobs if j["id"] not in seen_ids]
        lv_new += len(new_jobs)
        lv_skipped += len(jobs) - len(new_jobs)
        if new_jobs:
            print(f"  [lv] {name}: {len(new_jobs)} new / {len(jobs)} total")
        all_jobs.extend(new_jobs)
        time.sleep(INTER_REQUEST_DELAY)

    # Fetch Ashby
    print(f"[ats] Fetching Ashby boards...")
    for emp in ash_employers:
        slug = emp["atsSlug"]
        name = emp["name"]
        try:
            jobs = fetch_ashby(slug, name, cutoff=cutoff)
        except Exception as e:
            print(f"  [ash] {name}: unexpected error — {e}", file=sys.stderr)
            ash_errors += 1
            time.sleep(INTER_REQUEST_DELAY)
            continue

        new_jobs = [j for j in jobs if j["id"] not in seen_ids]
        ash_new += len(new_jobs)
        ash_skipped += len(jobs) - len(new_jobs)
        if new_jobs:
            print(f"  [ash] {name}: {len(new_jobs)} new / {len(jobs)} total")
        all_jobs.extend(new_jobs)
        time.sleep(INTER_REQUEST_DELAY)

    print(
        f"[ats] Done. Greenhouse: {gh_new} new, {gh_skipped} already-seen, {gh_errors} errors. "
        f"Lever: {lv_new} new, {lv_skipped} already-seen, {lv_errors} errors. "
        f"Ashby: {ash_new} new, {ash_skipped} already-seen, {ash_errors} errors. "
        f"Total new: {len(all_jobs)}"
    )

    if not all_jobs:
        print("[ats] No new jobs — writing empty output files.")
        _write_jsonl(run_dir / "scan_ats.jsonl", [])
        _write_jsonl(run_dir / "raw_ats.jsonl", [])
        return

    # Write scan_ats.jsonl (minimal — for jobscout_seen.py)
    scan_records = [
        {
            "id": j["id"],
            "title": j["title"],
            "companyName": j["companyName"],
            "location": j["location"],
            "postedAt": j["postedAt"],
            "_src": "ats",
            "source": j["source"],
        }
        for j in all_jobs
    ]
    _write_jsonl(run_dir / "scan_ats.jsonl", scan_records)
    print(f"[ats] Wrote {len(scan_records)} records to {run_dir}/scan_ats.jsonl")

    # Write raw_ats.jsonl (full records with descriptions — for LLM scoring)
    _write_jsonl(run_dir / "raw_ats.jsonl", all_jobs)
    print(f"[ats] Wrote {len(all_jobs)} records to {run_dir}/raw_ats.jsonl")


if __name__ == "__main__":
    main()
