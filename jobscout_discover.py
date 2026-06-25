"""jobscout_discover.py — ATS discovery pass across all known Greenhouse/Lever/Ashby companies.

Downloads public slug lists, probes each company's job board, and flags any company
that has a relevant-sounding job title AND at least one remote or US-based posting.

Results written to:
  <cache-dir>/discovery_results.json  — companies worth adding to target_employers.json
  <cache-dir>/discovery_errors.json   — slugs that failed (404, timeout, etc.) for follow-up

Slug lists are cached locally for 30 days to avoid re-downloading on repeat runs.

Usage:
  python3 jobscout_discover.py
  python3 jobscout_discover.py --limit 50
  python3 jobscout_discover.py --ats greenhouse --limit 100
  python3 jobscout_discover.py --cache-dir /tmp/jscout
"""

import argparse
import json
import re
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

# ---------------------------------------------------------------------------
# Slug list source URLs (Feashliaa/job-board-aggregator, CC BY-NC 4.0)
# ---------------------------------------------------------------------------

SLUG_LIST_URLS = {
    "greenhouse": "https://raw.githubusercontent.com/Feashliaa/job-board-aggregator/main/data/greenhouse_companies.json",
    "lever":      "https://raw.githubusercontent.com/Feashliaa/job-board-aggregator/main/data/lever_companies.json",
    "ashby":      "https://raw.githubusercontent.com/Feashliaa/job-board-aggregator/main/data/ashby_companies.json",
}

# Re-download slug lists if cache is older than this many days.
SLUG_CACHE_MAX_AGE_DAYS = 30

# ---------------------------------------------------------------------------
# ATS API endpoints (same as jobscout_ats_scraper.py)
# ---------------------------------------------------------------------------

GREENHOUSE_API = "https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=false"
LEVER_API      = "https://api.lever.co/v0/postings/{slug}?mode=json"
ASHBY_API      = "https://api.ashbyhq.com/posting-api/job-board/{slug}"

USER_AGENT      = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
REQUEST_TIMEOUT = 15
INTER_REQUEST_DELAY = 0.15  # seconds between requests

# ---------------------------------------------------------------------------
# Discovery keyword tiers
# ---------------------------------------------------------------------------

# Tier 1: Pass on title alone.
STRONG_TITLE_KEYWORDS = [
    # Knowledge / KM
    "knowledge management", "knowledge operations", "knowledge systems",
    "knowledge strategy", "knowledge strategist", "knowledge manager",
    "knowledge program manager", "knowledge base", "knowledge content",
    "knowledge governance", "knowledge lifecycle", "kcs",

    # Support content / scaled support
    "support content", "scaled support", "self-service", "customer self-service",
    "help center", "help centre", "help content", "support knowledge",
    "agent knowledge", "agent-facing content", "internal knowledge",
    "support documentation", "customer support content", "service content",
    "support content design", "self-service content", "help center content",
    "product support content",

    # Content strategy / content operations
    "content operations", "content strategy", "content strategist",
    "content program manager", "content governance", "content lifecycle",
    "content systems", "technical content", "technical writing",
    "technical writer", "documentation manager", "documentation program manager",

    # Digital adoption / workflow systems
    "digital adoption", "digital adoption platform", "digital employee experience",
    "digital enablement", "digital workplace", "workflow adoption",
    "software adoption", "product adoption", "user adoption",
    "change adoption", "walkme", "pendo", "whatfix",

    # AI-enabled support / AI-ready knowledge — specific phrases only
    "ai knowledge", "ai-ready knowledge", "ai-assisted support",
    "ai-enabled support", "genai support", "generative ai support",
    "chatbot content", "chatbot knowledge", "virtual assistant",
    "ai search", "ai support",
    "retrieval quality", "knowledge retrieval", "support retrieval",
    "rag knowledge", "rag content",
    "copilot knowledge", "copilot content", "copilot support",
    "support copilot", "agent copilot",
    "self-service search", "knowledge search", "support search",
    "agent enablement", "support enablement",
]

# Tier 2: Only pass when paired with a domain anchor below.
BROAD_ROLE_KEYWORDS = [
    "program manager", "technical program manager", "change management", "change manager",
]

# Tier 3: Domain anchors — validate broad role titles.
# "ai", "digital", and bare "search" excluded (too broad).
# "retrieval", "rag", "copilot" allowed here but not in STRONG_TITLE_KEYWORDS.
DOMAIN_ANCHORS = [
    "knowledge", "content", "documentation", "support", "self-service",
    "help center", "help centre", "adoption", "workflow",
    "chatbot", "genai", "taxonomy",
    "ai knowledge", "ai support", "ai search", "ai-enabled",
    "support search", "knowledge search", "self-service search",
    "copilot",
    "digital adoption", "digital enablement", "digital workplace", "digital employee experience",
]

# Titles matching any of these are rejected even if a positive keyword matches.
NEGATIVE_KEYWORDS = [
    # L&D / instructional design
    "instructional designer", "instructional design",
    "learning experience designer", "learning designer",
    "curriculum designer", "curriculum developer", "curriculum manager",
    "training specialist", "training manager", "corporate trainer", "facilitator",
    "lms administrator", "lms manager", "learning management system",
    "learning and development", "l&d", "ld specialist", "l&d specialist",
    "l&d manager", "talent development", "organizational development",
    "employee training", "sales training", "enablement training",
    "enablement trainer", "sales trainer", "training and development",
    "od consultant", "course designer", "course developer", "course development",
    "learning consultant", "e-learning", "elearning", "scorm",
    "articulate storyline", "captivate",
    # Engineering / data / ML
    "software engineer", "data engineer", "machine learning engineer",
    "ml engineer", "ai engineer", "research scientist", "data scientist",
    "knowledge graph engineer", "knowledge graph", "ontology engineer",
    "backend engineer", "frontend engineer", "devops", "site reliability",
    "sre", "systems engineer", "security engineer",
    "engineer",
    # Marketing / brand / growth
    "marketing program manager", "growth program manager", "brand program manager",
    "campaign program manager", "creative program manager", "social media",
    "content marketing", "digital marketing", "seo", "sem", "paid media",
    # Sales / revenue / partner
    "sales operations", "revenue operations", "partner program manager",
    "channel program manager", "sales enablement",
    # HR / people / talent
    "hr program manager", "people program manager", "talent program manager",
    "recruiting program manager", "employee engagement",
]

# ---------------------------------------------------------------------------
# US location detection — location string counts as US if it contains any of:
# ---------------------------------------------------------------------------

US_STATE_ABBREVS = {
    "al", "ak", "az", "ar", "ca", "co", "ct", "de", "fl", "ga", "hi", "id",
    "il", "in", "ia", "ks", "ky", "la", "me", "md", "ma", "mi", "mn", "ms",
    "mo", "mt", "ne", "nv", "nh", "nj", "nm", "ny", "nc", "nd", "oh", "ok",
    "or", "pa", "ri", "sc", "sd", "tn", "tx", "ut", "vt", "va", "wa", "wv",
    "wi", "wy", "dc",
}

US_STATE_NAMES = {
    "alabama", "alaska", "arizona", "arkansas", "california", "colorado",
    "connecticut", "delaware", "florida", "georgia", "hawaii", "idaho",
    "illinois", "indiana", "iowa", "kansas", "kentucky", "louisiana", "maine",
    "maryland", "massachusetts", "michigan", "minnesota", "mississippi",
    "missouri", "montana", "nebraska", "nevada", "new hampshire", "new jersey",
    "new mexico", "new york", "north carolina", "north dakota", "ohio",
    "oklahoma", "oregon", "pennsylvania", "rhode island", "south carolina",
    "south dakota", "tennessee", "texas", "utah", "vermont", "virginia",
    "washington", "west virginia", "wisconsin", "wyoming",
}

US_CITY_HINTS = {
    "san francisco", "new york", "los angeles", "chicago", "seattle", "austin",
    "boston", "denver", "atlanta", "miami", "portland", "phoenix", "dallas",
    "san jose", "san diego", "minneapolis", "detroit", "charlotte",
}


def _is_us_or_remote(location: str) -> bool:
    """Return True if a job location string looks like remote or US-based."""
    if not location:
        return True  # unspecified — give benefit of the doubt
    loc = location.lower()
    if "remote" in loc:
        return True
    if "united states" in loc or " us " in loc or loc.endswith(", us"):
        return True
    # Check state abbreviations as standalone tokens (e.g. ", CA" or "NY,")
    tokens = set(t.strip("(),. ") for t in loc.replace(",", " ").split())
    if tokens & US_STATE_ABBREVS:
        return True
    # Check full state names and major cities
    for name in US_STATE_NAMES | US_CITY_HINTS:
        if name in loc:
            return True
    return False


def _contains_term(text: str, term: str) -> bool:
    """Match term in text. Short acronyms use word boundaries; phrases use substring."""
    if len(term) <= 4 and " " not in term and term.replace("-", "").isalnum():
        return bool(re.search(rf"\b{re.escape(term)}\b", text))
    return term in text


def _matches_any(text: str, terms: list) -> bool:
    return any(_contains_term(text, t) for t in terms)


def _title_matches(title: str) -> bool:
    t = title.lower()
    if _matches_any(t, NEGATIVE_KEYWORDS):
        return False
    if _matches_any(t, STRONG_TITLE_KEYWORDS):
        return True
    return _matches_any(t, BROAD_ROLE_KEYWORDS) and _matches_any(t, DOMAIN_ANCHORS)


# ---------------------------------------------------------------------------
# HTTP helper
# ---------------------------------------------------------------------------

def _fetch_json(url: str):
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT) as r:
        return json.loads(r.read())


# ---------------------------------------------------------------------------
# Per-platform probers — return (matched_titles, matched_locations, total_jobs)
# or raise on error
# ---------------------------------------------------------------------------

def _probe_greenhouse(slug: str):
    data = _fetch_json(GREENHOUSE_API.format(slug=slug))
    jobs = data.get("jobs", [])
    matched_titles = []
    matched_locations = []
    for j in jobs:
        title = j.get("title", "")
        loc = (j.get("location") or {}).get("name") or ""
        if _title_matches(title) and _is_us_or_remote(loc):
            matched_titles.append(title)
            matched_locations.append(loc)
    return matched_titles, matched_locations, len(jobs)


def _probe_lever(slug: str):
    postings = _fetch_json(LEVER_API.format(slug=slug))
    if not isinstance(postings, list):
        return [], [], 0
    matched_titles = []
    matched_locations = []
    for p in postings:
        title = p.get("text", "")
        cats = p.get("categories") or {}
        locs = cats.get("allLocations") or ([cats.get("location")] if cats.get("location") else [])
        loc = locs[0] if locs else ""
        if _title_matches(title) and _is_us_or_remote(loc):
            matched_titles.append(title)
            matched_locations.append(loc)
    return matched_titles, matched_locations, len(postings)


def _probe_ashby(slug: str):
    data = _fetch_json(ASHBY_API.format(slug=slug))
    jobs = data.get("jobs", [])
    matched_titles = []
    matched_locations = []
    for j in jobs:
        title = j.get("title", "")
        loc = j.get("location") or ""
        is_remote = j.get("isRemote", False)
        wt = j.get("workplaceType") or ""
        if is_remote or wt == "Remote":
            loc = loc or "Remote"
        if _title_matches(title) and _is_us_or_remote(loc):
            matched_titles.append(title)
            matched_locations.append(loc)
    return matched_titles, matched_locations, len(jobs)


PROBERS = {
    "greenhouse": _probe_greenhouse,
    "lever": _probe_lever,
    "ashby": _probe_ashby,
}

# ---------------------------------------------------------------------------
# Slug list loading (with local cache)
# ---------------------------------------------------------------------------

def _load_slugs(ats: str, cache_dir: Path) -> list[str]:
    """Return slug list for `ats`, using local cache if fresh enough."""
    cache_path = cache_dir / f"discovery_slugs_{ats}.json"
    if cache_path.exists():
        try:
            cached = json.loads(cache_path.read_text())
            cached_at = datetime.fromisoformat(cached.get("cachedAt", "2000-01-01"))
            age_days = (datetime.now(timezone.utc) - cached_at.replace(tzinfo=timezone.utc)).days
            if age_days < SLUG_CACHE_MAX_AGE_DAYS:
                slugs = cached.get("slugs", [])
                print(f"[discover] {ats}: using cached slug list ({len(slugs)} slugs, {age_days}d old)")
                return slugs
        except Exception:
            pass

    print(f"[discover] {ats}: downloading slug list...", end=" ", flush=True)
    url = SLUG_LIST_URLS[ats]
    try:
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(req, timeout=30) as r:
            slugs = json.loads(r.read())
    except Exception as e:
        print(f"FAILED ({e})")
        return []
    print(f"{len(slugs)} slugs")
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(json.dumps({
        "ats": ats,
        "cachedAt": datetime.now(timezone.utc).isoformat(),
        "slugs": slugs,
    }, indent=2))
    return slugs


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Discover new employers across all Greenhouse/Lever/Ashby boards."
    )
    parser.add_argument("--ats", default="all", choices=["all", "greenhouse", "lever", "ashby"],
                        help="Which ATS to probe (default: all)")
    parser.add_argument("--limit", type=int, default=0,
                        help="Max slugs to probe per platform (0 = no limit; useful for testing)")
    parser.add_argument("--employers", default="config/target_employers.json",
                        help="Existing target employers file (slugs here are skipped)")
    parser.add_argument("--cache-dir", default="run",
                        help="Directory for slug list cache and output files (default: run)")
    args = parser.parse_args()

    cache_dir = Path(args.cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)

    # Load already-tracked slugs to skip
    tracked_slugs: set[str] = set()
    employers_path = Path(args.employers)
    if employers_path.exists():
        try:
            employers = json.loads(employers_path.read_text()).get("employers", [])
            tracked_slugs = {e["atsSlug"].lower() for e in employers if e.get("atsSlug")}
            print(f"[discover] Loaded {len(tracked_slugs)} already-tracked slugs to skip")
        except Exception as e:
            print(f"[discover] WARNING: could not load {employers_path}: {e}", file=sys.stderr)

    platforms = ["greenhouse", "lever", "ashby"] if args.ats == "all" else [args.ats]

    results = []
    errors = []
    now_date = datetime.now(timezone.utc).date().isoformat()

    for ats in platforms:
        slugs = _load_slugs(ats, cache_dir)
        new_slugs = [s for s in slugs if s.lower() not in tracked_slugs]
        if args.limit:
            new_slugs = new_slugs[:args.limit]

        probe = PROBERS[ats]
        hits = skipped = errs_404 = errs_timeout = errs_other = 0

        print(f"[discover] {ats}: probing {len(new_slugs)} slugs "
              f"({'limit ' + str(args.limit) if args.limit else 'all'}, "
              f"{len(slugs) - len(new_slugs)} already tracked)...")

        for i, slug in enumerate(new_slugs, 1):
            if i % 200 == 0:
                print(f"  [discover] {ats}: {i}/{len(new_slugs)} — "
                      f"{hits} hits, {errs_404} 404s, {errs_timeout} timeouts, {errs_other} other errors")
            try:
                matched_titles, matched_locations, total_jobs = probe(slug)
                if matched_titles:
                    results.append({
                        "ats": ats.capitalize() if ats != "ashby" else "Ashby",
                        "atsSlug": slug,
                        "suggestedName": slug,
                        "matchingTitles": matched_titles,
                        "matchingLocations": matched_locations,
                        "totalJobs": total_jobs,
                        "checkedAt": now_date,
                    })
                    hits += 1
                else:
                    skipped += 1
            except urllib.error.HTTPError as e:
                if e.code == 404:
                    errors.append({
                        "ats": ats,
                        "slug": slug,
                        "errorType": "404",
                        "detail": "Board not found",
                        "checkedAt": now_date,
                    })
                    errs_404 += 1
                else:
                    errors.append({
                        "ats": ats,
                        "slug": slug,
                        "errorType": f"http_{e.code}",
                        "detail": str(e),
                        "checkedAt": now_date,
                    })
                    errs_other += 1
            except urllib.error.URLError as e:
                reason = str(e.reason) if hasattr(e, "reason") else str(e)
                is_timeout = "timed out" in reason.lower() or "timeout" in reason.lower()
                errors.append({
                    "ats": ats,
                    "slug": slug,
                    "errorType": "timeout" if is_timeout else "url_error",
                    "detail": reason,
                    "checkedAt": now_date,
                })
                if is_timeout:
                    errs_timeout += 1
                else:
                    errs_other += 1
            except Exception as e:
                errors.append({
                    "ats": ats,
                    "slug": slug,
                    "errorType": "other",
                    "detail": str(e),
                    "checkedAt": now_date,
                })
                errs_other += 1

            time.sleep(INTER_REQUEST_DELAY)

        print(f"[discover] {ats}: done — {hits} hits, {skipped} no match, "
              f"{errs_404} 404s, {errs_timeout} timeouts, {errs_other} other errors")

    # Write results
    results_path = cache_dir / "discovery_results.json"
    errors_path  = cache_dir / "discovery_errors.json"

    results_path.write_text(json.dumps(results, indent=2))
    errors_path.write_text(json.dumps(errors, indent=2))

    print(f"\n[discover] Results: {len(results)} companies → {results_path}")
    print(f"[discover] Errors:  {len(errors)} slugs   → {errors_path}")


if __name__ == "__main__":
    main()
