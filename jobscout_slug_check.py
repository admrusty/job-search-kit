"""jobscout_slug_check.py — Validate ATS slugs in target_employers.json.

For each employer with a Greenhouse/Lever/Ashby slug, probes the public API.
On 404, generates name-based slug candidates, cross-references against GitHub
slug lists (Feashliaa/job-board-aggregator), and API-verifies survivors.

Usage:
  python3 jobscout_slug_check.py \
      --employers config/target_employers.json \
      --cache-dir config/slug_cache \
      --out run/slug_check_report.json \
      [--refresh]
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

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

GREENHOUSE_API = "https://boards-api.greenhouse.io/v1/boards/{slug}/jobs"
LEVER_API      = "https://api.lever.co/v0/postings/{slug}"
ASHBY_API      = "https://api.ashbyhq.com/posting-api/job-board/{slug}"

GITHUB_SLUG_LISTS = {
    "Greenhouse": "https://raw.githubusercontent.com/Feashliaa/job-board-aggregator/main/data/greenhouse_companies.json",
    "Lever":      "https://raw.githubusercontent.com/Feashliaa/job-board-aggregator/main/data/lever_companies.json",
    "Ashby":      "https://raw.githubusercontent.com/Feashliaa/job-board-aggregator/main/data/ashby_companies.json",
}

CACHE_MAX_AGE_DAYS = 7
REQUEST_TIMEOUT    = 12
INTER_REQUEST_DELAY = 0.15
USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"

# Legal suffixes to strip when normalizing company names for slug generation
_LEGAL_SUFFIXES = re.compile(
    r"\b(inc|llc|corp|ltd|co|gmbh|ag|bv|plc|sa|sas|nv|oy|as|ab|pty|pte|pvt|limited|incorporated|corporation|company)\b",
    re.IGNORECASE,
)

# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------

def _fetch(url: str, timeout: int = REQUEST_TIMEOUT) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def _probe_slug(ats: str, slug: str) -> tuple[str, int]:
    """Return (status, job_count). status: 'valid'|'valid_empty'|'not_found'|'error'."""
    urls = {
        "Greenhouse": GREENHOUSE_API.format(slug=slug),
        "Lever":      LEVER_API.format(slug=slug),
        "Ashby":      ASHBY_API.format(slug=slug),
    }
    url = urls.get(ats)
    if not url:
        return "unsupported", 0
    try:
        data = json.loads(_fetch(url))
        if ats == "Greenhouse":
            count = len(data.get("jobs", []))
        elif ats == "Lever":
            count = len(data) if isinstance(data, list) else 0
        else:  # Ashby
            count = len(data.get("jobs", []))
        return ("valid_empty" if count == 0 else "valid"), count
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return "not_found", 0
        return "error", 0
    except Exception:
        return "error", 0


# ---------------------------------------------------------------------------
# GitHub slug-list cache
# ---------------------------------------------------------------------------

def _cache_path(cache_dir: Path, ats: str) -> Path:
    return cache_dir / f"{ats.lower()}_slugs.json"


def _load_slug_list(cache_dir: Path, ats: str, refresh: bool) -> set[str]:
    """Return set of known slugs for an ATS from the GitHub cache."""
    path = _cache_path(cache_dir, ats)
    url  = GITHUB_SLUG_LISTS.get(ats)
    if not url:
        return set()

    needs_download = refresh or not path.exists()
    if not needs_download and path.exists():
        age = datetime.now(tz=timezone.utc) - datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
        if age > timedelta(days=CACHE_MAX_AGE_DAYS):
            needs_download = True

    if needs_download:
        print(f"[slug-check] Downloading {ats} slug list from GitHub...", file=sys.stderr)
        try:
            raw = _fetch(url, timeout=20)
            slugs = json.loads(raw)
            cache_dir.mkdir(parents=True, exist_ok=True)
            path.write_bytes(raw)
            print(f"[slug-check]   {ats}: {len(slugs)} slugs cached.", file=sys.stderr)
        except Exception as e:
            print(f"[slug-check]   WARNING: could not download {ats} slug list: {e}", file=sys.stderr)
            return set()

    try:
        return set(json.loads(path.read_text()))
    except Exception:
        return set()


# ---------------------------------------------------------------------------
# Slug candidate generation
# ---------------------------------------------------------------------------

def _normalize_name(name: str) -> str:
    """Strip legal suffixes, punctuation, extra spaces; lowercase."""
    n = name.lower()
    n = _LEGAL_SUFFIXES.sub("", n)
    n = re.sub(r"[^a-z0-9\s]", "", n)
    n = re.sub(r"\s+", " ", n).strip()
    return n


def _slug_candidates(name: str) -> list[str]:
    """Generate plausible slug variations from a company name."""
    norm = _normalize_name(name)
    words = norm.split()
    base  = norm.replace(" ", "")        # e.g. "acmecorp"
    dash  = norm.replace(" ", "-")       # e.g. "acme-corp"
    first = words[0] if words else base  # e.g. "acme"

    candidates = []
    seen = set()

    def add(s: str) -> None:
        s = s.strip("-").lower()
        if s and s not in seen:
            seen.add(s)
            candidates.append(s)

    add(base)
    add(dash)
    add(first)
    add(f"{base}inc")
    add(f"{base}hq")
    add(f"{first}inc")
    add(f"{first}hq")
    add(f"{base}-2")
    add(f"{first}-2")
    # Also try without common short words (the, my, get)
    filtered = [w for w in words if w not in {"the", "my", "get", "go", "use"}]
    if filtered != words:
        add("".join(filtered))
        add("-".join(filtered))

    return candidates


def _find_suggestions(name: str, ats: str, known_slugs: set[str]) -> list[dict]:
    """Generate candidates, cross-ref with known slugs, API-verify survivors."""
    candidates = _slug_candidates(name)
    # Prioritise candidates in the GitHub list; always include the rest
    in_list  = [c for c in candidates if c in known_slugs]
    not_list = [c for c in candidates if c not in known_slugs]
    ordered  = in_list + not_list

    suggestions = []
    for slug in ordered[:12]:  # probe at most 12 candidates
        status, count = _probe_slug(ats, slug)
        time.sleep(INTER_REQUEST_DELAY)
        if status in ("valid", "valid_empty"):
            suggestions.append({
                "slug": slug,
                "jobCount": count,
                "inGithubList": slug in known_slugs,
            })
        if len(suggestions) >= 3:
            break

    return suggestions


# ---------------------------------------------------------------------------
# Main check loop
# ---------------------------------------------------------------------------

def run_check(employers_path: Path, cache_dir: Path, out_path: Path, refresh: bool) -> dict:
    employers = json.loads(employers_path.read_text()).get("employers", [])
    active = [
        e for e in employers
        if e.get("ats") in ("Greenhouse", "Lever", "Ashby") and e.get("atsSlug")
    ]

    print(f"[slug-check] Checking {len(active)} active slugs...", file=sys.stderr)

    # Load GitHub slug lists once per ATS
    slug_lists: dict[str, set[str]] = {}
    for ats in ("Greenhouse", "Lever", "Ashby"):
        slug_lists[ats] = _load_slug_list(cache_dir, ats, refresh)

    results = {"valid": [], "validEmpty": [], "failed": []}

    for emp in active:
        name = emp["name"]
        ats  = emp["ats"]
        slug = emp["atsSlug"]

        status, count = _probe_slug(ats, slug)
        time.sleep(INTER_REQUEST_DELAY)

        if status == "valid":
            results["valid"].append({"name": name, "ats": ats, "slug": slug, "jobCount": count})
            print(f"  ✓ {name} ({ats}: {slug}) — {count} jobs", file=sys.stderr)
        elif status == "valid_empty":
            results["validEmpty"].append({"name": name, "ats": ats, "slug": slug})
            print(f"  ○ {name} ({ats}: {slug}) — board exists, 0 jobs", file=sys.stderr)
        else:
            print(f"  ✗ {name} ({ats}: {slug}) — {status}; searching for suggestions...", file=sys.stderr)
            suggestions = _find_suggestions(name, ats, slug_lists.get(ats, set()))
            results["failed"].append({
                "name": name,
                "ats": ats,
                "slug": slug,
                "status": status,
                "suggestions": suggestions,
            })

    report = {
        "checkedAt":   datetime.now(tz=timezone.utc).isoformat(),
        "totalChecked": len(active),
        "validCount":  len(results["valid"]),
        "emptyCount":  len(results["validEmpty"]),
        "failedCount": len(results["failed"]),
        "failed":      results["failed"],
    }

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(report, indent=2))
    print(
        f"[slug-check] Done. {report['validCount']} valid, {report['emptyCount']} empty boards, "
        f"{report['failedCount']} failed. Report → {out_path}",
        file=sys.stderr,
    )
    return report


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="Validate ATS slugs in target_employers.json.")
    parser.add_argument("--employers", default="config/target_employers.json")
    parser.add_argument("--cache-dir", default="config/slug_cache")
    parser.add_argument("--out", default="run/slug_check_report.json")
    parser.add_argument("--refresh", action="store_true",
                        help="Force re-download of GitHub slug lists even if cache is fresh.")
    args = parser.parse_args()

    report = run_check(
        employers_path=Path(args.employers),
        cache_dir=Path(args.cache_dir),
        out_path=Path(args.out),
        refresh=args.refresh,
    )

    if report["failedCount"] > 0:
        sys.exit(1)


if __name__ == "__main__":
    main()
