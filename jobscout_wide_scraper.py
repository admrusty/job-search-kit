"""jobscout_wide_scraper.py — Wide ATS scraper across all public Greenhouse/Lever/Ashby boards.

Two-pass approach:
  Pass 1: Fetch job titles only (fast, no descriptions) for all ~15,862 slugs using a
          thread pool. Filter titles against a keyword list.
  Pass 2: Re-fetch Greenhouse boards that had keyword matches with ?content=true.
          Lever/Ashby already return descriptions in Pass 1.

Outputs (written to --run-dir):
  scan_wide.jsonl   minimal records for jobscout_seen.py deduplication
  raw_wide.jsonl    full records with descriptions, picked up by SKILL.md Step 7b

Skips slugs already covered by target_employers.json (handled by jobscout_ats_scraper.py).
Skips job IDs already in job-scout-seen.json.

Usage:
  python3 jobscout_wide_scraper.py \\
      --employers config/target_employers.json \\
      --slugs     config/slug_cache \\
      --seen      job-scout-seen.json \\
      --config    config/jobscout_config.json \\
      --run-dir   run \\
      --max-age-days 7 \\
      --workers   10
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

GREENHOUSE_TITLES_API = "https://boards-api.greenhouse.io/v1/boards/{slug}/jobs"
GREENHOUSE_FULL_API   = "https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=true"
LEVER_API             = "https://api.lever.co/v0/postings/{slug}"
ASHBY_API             = "https://api.ashbyhq.com/posting-api/job-board/{slug}"

USER_AGENT      = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
REQUEST_TIMEOUT = 15

DEFAULT_TITLE_KEYWORDS = [
    "knowledge manager", "knowledge management", "knowledge operations",
    "knowledge strategist", "knowledge program", "knowledge base", "knowledge specialist",
    "content operations", "content strategist", "content program", "content governance",
    "content ops", "content lifecycle", "support content", "help center",
    "digital adoption", "digital employee experience", "digital enablement",
    "ai enablement", "ai adoption", "ai knowledge", "ai transformation",
    "learning technology", "learning systems", "learning operations", "learning manager",
    "lms administrator", "instructional designer",
    "enablement manager", "information architecture", "kcs",
    "change management", "workflow transformation",
    "training manager", "program manager",
]


# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------

def _fetch_json(url: str, timeout: int = REQUEST_TIMEOUT):
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def _strip_html(html: str) -> str:
    if not html:
        return ""
    text = re.sub(r"<[^>]+>", " ", html)
    for ent, rep in [("&nbsp;", " "), ("&amp;", "&"), ("&lt;", "<"),
                     ("&gt;", ">"), ("&quot;", '"'), ("&#39;", "'")]:
        text = text.replace(ent, rep)
    return re.sub(r"\s+", " ", text).strip()


def _load_seen_ids(seen_path: Path) -> set:
    if not seen_path.exists():
        return set()
    try:
        data = json.loads(seen_path.read_text())
        if isinstance(data, list):
            return set(str(x) for x in data)
        return set(str(x) for x in data.get("ids", []))
    except Exception:
        return set()


def _write_jsonl(path: Path, records: list) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for rec in records:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def _cutoff_date(max_age_days: int) -> str:
    return (datetime.now(tz=timezone.utc) - timedelta(days=max_age_days)).date().isoformat()


# ---------------------------------------------------------------------------
# Title keyword matching
# ---------------------------------------------------------------------------

def _title_matches(title: str, keywords: list[str]) -> bool:
    t = title.lower()
    return any(kw in t for kw in keywords)


# ---------------------------------------------------------------------------
# Pass 1: fetch titles only
# ---------------------------------------------------------------------------

def _pass1_greenhouse(slug: str, cutoff: str, keywords: list[str]) -> dict | None:
    """Return {slug, matched_job_ids} or None on error/404."""
    try:
        data = _fetch_json(GREENHOUSE_TITLES_API.format(slug=slug))
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None
        return None
    except Exception:
        return None

    jobs = data.get("jobs", [])
    matched = []
    for j in jobs:
        posted_raw = j.get("first_published") or j.get("updated_at") or ""
        posted = posted_raw[:10] if posted_raw else ""
        if cutoff and posted and posted < cutoff:
            continue
        title = j.get("title", "")
        if _title_matches(title, keywords):
            matched.append(j["id"])

    if not matched:
        return None
    return {"slug": slug, "matched_job_ids": matched}


def _pass1_lever(slug: str, cutoff: str, keywords: list[str], seen_ids: set, now_iso: str) -> list:
    """Lever returns full records — filter by title and return job records directly."""
    try:
        postings = _fetch_json(LEVER_API.format(slug=slug))
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return []
        return []
    except Exception:
        return []

    if not isinstance(postings, list):
        return []

    out = []
    for p in postings:
        title = p.get("text", "")
        if not _title_matches(title, keywords):
            continue

        ts = p.get("createdAt") or 0
        try:
            posted = datetime.fromtimestamp(int(ts) / 1000, tz=timezone.utc).date().isoformat() if ts else ""
        except Exception:
            posted = ""
        if cutoff and posted and posted < cutoff:
            continue

        job_id = f"lv_{p['id']}"
        if job_id in seen_ids:
            continue

        cats = p.get("categories") or {}
        loc = cats.get("location") or (cats.get("allLocations") or [""])[0]
        desc_html = (p.get("description") or "") + (p.get("additional") or "")
        desc_plain = (p.get("descriptionPlain") or "") + (p.get("additionalPlain") or "")
        description = desc_plain.strip() if desc_plain.strip() else _strip_html(desc_html)

        wt = (p.get("workplaceType") or "").lower()
        workplace = {"remote": "Remote", "hybrid": "Hybrid", "onsite": "On-site"}.get(wt, "")

        out.append({
            "id": job_id,
            "title": title,
            "companyName": slug,
            "company": slug,
            "location": str(loc),
            "postedAt": posted,
            "posted": posted,
            "link": p.get("hostedUrl", ""),
            "source": "lever",
            "_src": "wide",
            "addedAt": now_iso,
            "workplace": workplace,
            "description": description,
        })
    return out


def _pass1_ashby(slug: str, cutoff: str, keywords: list[str], seen_ids: set, now_iso: str) -> list:
    """Ashby returns full records — filter by title and return job records directly."""
    try:
        data = _fetch_json(ASHBY_API.format(slug=slug))
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return []
        return []
    except Exception:
        return []

    jobs = data.get("jobs", [])
    out = []
    for j in jobs:
        title = j.get("title", "")
        if not _title_matches(title, keywords):
            continue

        posted_raw = j.get("publishedAt") or ""
        posted = posted_raw[:10] if posted_raw else ""
        if cutoff and posted and posted < cutoff:
            continue

        job_id = f"ash_{j['id']}"
        if job_id in seen_ids:
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
            "id": job_id,
            "title": title,
            "companyName": slug,
            "company": slug,
            "location": j.get("location") or "",
            "postedAt": posted,
            "posted": posted,
            "link": j.get("jobUrl", ""),
            "source": "ashby",
            "_src": "wide",
            "addedAt": now_iso,
            "workplace": workplace,
            "description": description,
        })
    return out


# ---------------------------------------------------------------------------
# Pass 2: fetch Greenhouse descriptions for matched boards
# ---------------------------------------------------------------------------

def _pass2_greenhouse(slug: str, matched_ids: set, cutoff: str, seen_ids: set, now_iso: str) -> list:
    """Fetch full descriptions for a Greenhouse board and return matched jobs."""
    try:
        data = _fetch_json(GREENHOUSE_FULL_API.format(slug=slug))
    except Exception:
        return []

    jobs = data.get("jobs", [])
    out = []
    for j in jobs:
        if j["id"] not in matched_ids:
            continue

        job_id = f"gh_{j['id']}"
        if job_id in seen_ids:
            continue

        posted_raw = j.get("first_published") or j.get("updated_at") or ""
        posted = posted_raw[:10] if posted_raw else ""
        if cutoff and posted and posted < cutoff:
            continue

        loc = (j.get("location") or {}).get("name") or ""
        description = _strip_html(j.get("content") or "")
        loc_lower = loc.lower()
        if "remote" in loc_lower:
            workplace = "Remote"
        elif "hybrid" in loc_lower:
            workplace = "Hybrid"
        else:
            workplace = ""

        out.append({
            "id": job_id,
            "title": j.get("title", ""),
            "companyName": slug,
            "company": slug,
            "location": loc,
            "postedAt": posted,
            "posted": posted,
            "link": j.get("absolute_url", ""),
            "source": "greenhouse",
            "_src": "wide",
            "addedAt": now_iso,
            "workplace": workplace,
            "description": description,
        })
    return out


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Wide ATS scraper across all public boards.")
    parser.add_argument("--employers", default="config/target_employers.json",
                        help="Target employers (to skip already-covered slugs)")
    parser.add_argument("--slugs", default="config/slug_cache",
                        help="Directory containing greenhouse_slugs.json, lever_slugs.json, ashby_slugs.json")
    parser.add_argument("--seen", default="job-scout-seen.json",
                        help="Path to job-scout-seen.json")
    parser.add_argument("--config", default="config/jobscout_config.json",
                        help="Path to jobscout_config.json (for titleKeywords)")
    parser.add_argument("--run-dir", default="run",
                        help="Output directory")
    parser.add_argument("--max-age-days", type=int, default=7,
                        help="Only include jobs posted within this many days (0 = no filter)")
    parser.add_argument("--max-age-hours", type=int, default=0,
                        help="Lookback window in hours; overrides --max-age-days when > 0")
    parser.add_argument("--workers", type=int, default=10,
                        help="Number of concurrent HTTP threads (default: 10)")
    args = parser.parse_args()

    run_dir   = Path(args.run_dir)
    slugs_dir = Path(args.slugs)
    now_iso   = datetime.now(tz=timezone.utc).isoformat()
    if args.max_age_hours > 0:
        cutoff = (datetime.now(tz=timezone.utc) - timedelta(hours=args.max_age_hours)).date().isoformat()
    else:
        cutoff = _cutoff_date(args.max_age_days) if args.max_age_days > 0 else ""

    # Load title keywords from config, falling back to defaults
    title_keywords = DEFAULT_TITLE_KEYWORDS
    try:
        cfg = json.loads(Path(args.config).read_text())
        kw_override = cfg.get("wideScraper", {}).get("titleKeywords")
        if kw_override:
            title_keywords = [k.lower() for k in kw_override]
    except Exception:
        pass
    print(f"[wide] Title keywords: {len(title_keywords)} terms")

    # Load slug lists
    def load_slugs(fname: str) -> list[str]:
        p = slugs_dir / fname
        if not p.exists():
            print(f"[wide] WARNING: {p} not found — skipping", file=sys.stderr)
            return []
        return json.loads(p.read_text())

    gh_all  = load_slugs("greenhouse_slugs.json")
    lv_all  = load_slugs("lever_slugs.json")
    ash_all = load_slugs("ashby_slugs.json")
    print(f"[wide] Slug lists loaded: {len(gh_all)} GH, {len(lv_all)} LV, {len(ash_all)} ASH")

    # Build exclusion set from target_employers.json (already handled by ats_scraper)
    excluded_gh  = set()
    excluded_lv  = set()
    excluded_ash = set()
    try:
        employers = json.loads(Path(args.employers).read_text()).get("employers", [])
        for e in employers:
            ats  = e.get("ats", "")
            slug = e.get("atsSlug", "")
            if not slug:
                continue
            if ats == "Greenhouse":
                excluded_gh.add(slug)
            elif ats == "Lever":
                excluded_lv.add(slug)
            elif ats == "Ashby":
                excluded_ash.add(slug)
    except Exception as ex:
        print(f"[wide] WARNING: could not load employers file: {ex}", file=sys.stderr)

    gh_slugs  = [s for s in gh_all  if s not in excluded_gh]
    lv_slugs  = [s for s in lv_all  if s not in excluded_lv]
    ash_slugs = [s for s in ash_all if s not in excluded_ash]
    print(f"[wide] After excluding target employers: {len(gh_slugs)} GH, {len(lv_slugs)} LV, {len(ash_slugs)} ASH")

    # Load seen IDs
    seen_ids = _load_seen_ids(Path(args.seen))
    print(f"[wide] Loaded {len(seen_ids)} seen IDs")
    if cutoff:
        win = f"{args.max_age_hours}h" if args.max_age_hours > 0 else f"{args.max_age_days} days"
        print(f"[wide] Date cutoff: {cutoff} ({win})")

    all_jobs: list[dict] = []
    gh_boards_matched = 0
    gh_boards_scanned = 0
    lv_jobs_found     = 0
    ash_jobs_found    = 0
    p1_errors         = 0

    # -----------------------------------------------------------------------
    # Pass 1a — Greenhouse title scan (parallel)
    # -----------------------------------------------------------------------
    print(f"[wide] Pass 1a: scanning {len(gh_slugs)} Greenhouse boards for title matches...")
    gh_matched_boards: dict[str, set] = {}  # slug -> set of matched int IDs

    def _gh_p1_task(slug):
        return _pass1_greenhouse(slug, cutoff, title_keywords)

    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(_gh_p1_task, s): s for s in gh_slugs}
        done = 0
        for fut in as_completed(futures):
            done += 1
            if done % 500 == 0:
                print(f"  [wide/gh-p1] {done}/{len(gh_slugs)} scanned, {len(gh_matched_boards)} boards matched so far")
            result = fut.result()
            if result:
                gh_matched_boards[result["slug"]] = set(result["matched_job_ids"])

    gh_boards_scanned = len(gh_slugs)
    gh_boards_matched = len(gh_matched_boards)
    total_matched_titles = sum(len(v) for v in gh_matched_boards.values())
    print(f"[wide] Pass 1a done: {gh_boards_matched}/{gh_boards_scanned} GH boards had keyword matches ({total_matched_titles} title matches)")

    # -----------------------------------------------------------------------
    # Pass 1b — Lever (parallel, full records)
    # -----------------------------------------------------------------------
    print(f"[wide] Pass 1b: scanning {len(lv_slugs)} Lever boards...")

    def _lv_task(slug):
        return _pass1_lever(slug, cutoff, title_keywords, seen_ids, now_iso)

    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(_lv_task, s): s for s in lv_slugs}
        done = 0
        for fut in as_completed(futures):
            done += 1
            if done % 500 == 0:
                print(f"  [wide/lv] {done}/{len(lv_slugs)} scanned")
            jobs = fut.result()
            if jobs:
                all_jobs.extend(jobs)

    lv_jobs_found = sum(1 for j in all_jobs if j["source"] == "lever")
    print(f"[wide] Pass 1b done: {lv_jobs_found} Lever jobs matched")

    # -----------------------------------------------------------------------
    # Pass 1c — Ashby (parallel, full records)
    # -----------------------------------------------------------------------
    print(f"[wide] Pass 1c: scanning {len(ash_slugs)} Ashby boards...")

    def _ash_task(slug):
        return _pass1_ashby(slug, cutoff, title_keywords, seen_ids, now_iso)

    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(_ash_task, s): s for s in ash_slugs}
        done = 0
        for fut in as_completed(futures):
            done += 1
            if done % 500 == 0:
                print(f"  [wide/ash] {done}/{len(ash_slugs)} scanned")
            jobs = fut.result()
            if jobs:
                all_jobs.extend(jobs)

    ash_jobs_found = sum(1 for j in all_jobs if j["source"] == "ashby")
    print(f"[wide] Pass 1c done: {ash_jobs_found} Ashby jobs matched")

    # -----------------------------------------------------------------------
    # Pass 2 — Greenhouse descriptions for matched boards
    # -----------------------------------------------------------------------
    print(f"[wide] Pass 2: fetching descriptions for {gh_boards_matched} Greenhouse boards...")

    def _gh_p2_task(item):
        slug, matched_ids = item
        return _pass2_greenhouse(slug, matched_ids, cutoff, seen_ids, now_iso)

    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(_gh_p2_task, item): item[0] for item in gh_matched_boards.items()}
        done = 0
        for fut in as_completed(futures):
            done += 1
            jobs = fut.result()
            if jobs:
                all_jobs.extend(jobs)

    gh_jobs_found = sum(1 for j in all_jobs if j["source"] == "greenhouse")
    print(f"[wide] Pass 2 done: {gh_jobs_found} Greenhouse jobs with descriptions")

    total = len(all_jobs)
    print(f"[wide] Total new jobs: {total} (GH: {gh_jobs_found}, LV: {lv_jobs_found}, ASH: {ash_jobs_found})")

    # -----------------------------------------------------------------------
    # Write output
    # -----------------------------------------------------------------------
    if not all_jobs:
        print("[wide] No new matching jobs — writing empty output files.")
        _write_jsonl(run_dir / "scan_wide.jsonl", [])
        _write_jsonl(run_dir / "raw_wide.jsonl", [])
        return

    scan_records = [
        {
            "id": j["id"],
            "title": j["title"],
            "companyName": j["companyName"],
            "location": j["location"],
            "postedAt": j["postedAt"],
            "_src": "wide",
            "source": j["source"],
        }
        for j in all_jobs
    ]
    _write_jsonl(run_dir / "scan_wide.jsonl", scan_records)
    print(f"[wide] Wrote {len(scan_records)} records to {run_dir}/scan_wide.jsonl")

    _write_jsonl(run_dir / "raw_wide.jsonl", all_jobs)
    print(f"[wide] Wrote {len(all_jobs)} records to {run_dir}/raw_wide.jsonl")


if __name__ == "__main__":
    main()
