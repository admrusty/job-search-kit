"""Job Scout — Weekly Metrics Report Generator (Wave 5, WP-S).

Generates a Markdown report summarising pipeline activity over the past N days.

Usage:
    python3 jobscout_weekly.py \\
        --data data.json \\
        --applied job-scout-applied.json \\
        --state job-scout-state.json \\
        --run-summaries run/ \\
        --out reports/jobscout_weekly_YYYY-MM-DD.md \\
        --days 7

All paths default to locations relative to the script directory.
--days defaults to 7.
--out defaults to reports/jobscout_weekly_{today}.md.

# To run weekly: add to SKILL.md Step 14 (git commit) on Sunday runs:
# python3 jobscout_weekly.py --data data.json --applied job-scout-applied.json
"""

import argparse
import json
import logging
import os
import sys
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
log = logging.getLogger(__name__)

SCRIPT_DIR = Path(__file__).parent.resolve()

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _load_json(path: Path, label: str):
    """Load JSON from path. Returns None on any error; logs a warning."""
    if not path.exists():
        log.warning("File not found, skipping %s section: %s", label, path)
        return None
    try:
        with open(path) as f:
            return json.load(f)
    except Exception as exc:
        log.warning("Could not load %s (%s): %s", label, path, exc)
        return None


def _parse_ts(ts_str) -> Optional[datetime]:
    """Parse an ISO-8601 timestamp string to a UTC-aware datetime. Returns None on failure."""
    if not ts_str:
        return None
    s = str(ts_str).strip()
    # Remove trailing Z; handle +00:00 / offset patterns
    for fmt in (
        "%Y-%m-%dT%H:%M:%S.%f%z",
        "%Y-%m-%dT%H:%M:%S%z",
        "%Y-%m-%dT%H:%M:%S.%f",
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%d",
    ):
        try:
            dt = datetime.strptime(s[:len(fmt) + 6], fmt)  # rough length trim
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc)
        except ValueError:
            pass
    # Fallback: try dateutil if available
    try:
        from dateutil import parser as dup
        dt = dup.parse(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        pass
    return None


def _within_window(ts_str, cutoff: datetime) -> bool:
    """Return True if ts_str parses to a datetime >= cutoff."""
    dt = _parse_ts(ts_str)
    if dt is None:
        return False
    return dt >= cutoff


def _pct(num, denom, decimals=1) -> str:
    if not denom:
        return "n/a"
    return f"{round(100 * num / denom, decimals):.{decimals}f}%"


def _score_bracket(score) -> str:
    """Map a score to its 2-wide bracket label."""
    try:
        s = float(score)
    except (TypeError, ValueError):
        return "unscored"
    if s <= 0:
        return "0"
    pairs = [(1, 2), (3, 4), (5, 6), (7, 8), (9, 10)]
    for lo, hi in pairs:
        if lo <= s <= hi:
            return f"{lo}-{hi}"
    return str(int(s))


def _md_table(headers: list[str], rows: list[list]) -> str:
    """Build a Markdown pipe table."""
    if not rows:
        return ""
    widths = [max(len(str(h)), max((len(str(r[i])) for r in rows), default=0))
              for i, h in enumerate(headers)]
    def _row(cells):
        return "| " + " | ".join(str(c).ljust(widths[i]) for i, c in enumerate(cells)) + " |"
    sep = "| " + " | ".join("-" * w for w in widths) + " |"
    lines = [_row(headers), sep] + [_row(r) for r in rows]
    return "\n".join(lines)


def _salary_display(job: dict) -> str:
    """Return the best salary display string for a job."""
    return (
        job.get("salary")
        or job.get("salaryRaw")
        or ""
    )


# ---------------------------------------------------------------------------
# Data loading helpers
# ---------------------------------------------------------------------------

def _get_jobs_list(data) -> list[dict]:
    """Extract the jobs list from loaded data.json content."""
    if data is None:
        return []
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        # Canonical schema: {"jobs": [...]}
        if "jobs" in data and isinstance(data["jobs"], list):
            return data["jobs"]
        # Older schema keyed by ID: {"12345": {...}, ...}
        if all(isinstance(v, dict) for v in data.values()):
            return list(data.values())
    return []


def _job_timestamp(job: dict):
    """Return the best available creation timestamp for a job."""
    return job.get("addedAt") or job.get("createdAt") or job.get("postedAt") or job.get("posted")


def _is_remote(job: dict) -> bool:
    wp = (job.get("workplaceNormalized") or job.get("workplace") or "").lower()
    src = (job.get("src") or job.get("sourceRun") or "").lower()
    return "remote" in wp or src == "remote"


def _is_dismissed(job: dict) -> bool:
    return bool(
        job.get("dismissed")
        or job.get("userDismissed")
        or (job.get("reviewAction") or "").lower() == "dismiss"
        or (job.get("status") or "").lower() == "dismissed"
    )


def _is_auto_suppressed(job: dict) -> bool:
    return bool(job.get("autoSuppressed"))


def _is_alert(job: dict) -> bool:
    """Alert = score >= 7, not dismissed, reviewAction != Dismiss."""
    score = job.get("score") or 0
    try:
        score = float(score)
    except (TypeError, ValueError):
        score = 0
    if score < 7:
        return False
    ra = (job.get("reviewAction") or "").lower()
    if ra == "dismiss":
        return False
    if _is_dismissed(job):
        return False
    return True


def _search_lane(job: dict) -> str:
    """Return the search lane; fall back to category or src."""
    return (
        job.get("searchLane")
        or job.get("category")
        or job.get("src")
        or "unknown"
    )


# ---------------------------------------------------------------------------
# Section renderers
# ---------------------------------------------------------------------------

def section_header(today: str, generated: str) -> str:
    return f"# Job Scout Weekly Report — Week of {today}\nGenerated: {generated}\n"


def section_pipeline_summary(new_jobs: list[dict], run_summaries: list[dict]) -> str:
    lines = ["## 2. Pipeline Summary\n"]

    total = len(new_jobs)
    remote_ct = sum(1 for j in new_jobs if _is_remote(j))
    oc_ct = sum(1 for j in new_jobs if (j.get("src") or j.get("sourceRun") or "") == "oc")

    # Source breakdown: LinkedIn (src=remote/oc), ATS/wide scraper (src=wide/ats), other
    def _src_val(j):
        return (j.get("src") or j.get("sourceRun") or "").lower()

    li_ct = sum(1 for j in new_jobs if _src_val(j) in ("remote", "oc"))
    ats_ct = sum(1 for j in new_jobs if _src_val(j) in ("wide", "ats"))
    other_ct = total - li_ct - ats_ct

    scored = [j for j in new_jobs if j.get("score") is not None]
    scored_ct = len(scored)

    brackets = Counter(_score_bracket(j["score"]) for j in scored)
    bracket_order = ["1-2", "3-4", "5-6", "7-8", "9-10"]

    suppressed_ct = sum(1 for j in new_jobs if _is_auto_suppressed(j))
    alert_ct = sum(1 for j in new_jobs if _is_alert(j))

    src_parts = [f"LinkedIn: {li_ct}", f"ATS: {ats_ct}"]
    if other_ct:
        src_parts.append(f"other: {other_ct}")
    lines.append(f"- **New jobs scraped:** {total} ({', '.join(src_parts)})")
    lines.append(f"  - LinkedIn: remote {remote_ct}, OC {oc_ct}")
    lines.append(f"- **New jobs scored** (description fetched): {scored_ct}")
    lines.append(f"- **Auto-suppressed:** {suppressed_ct}")
    lines.append(f"- **New alerts generated** (score ≥ 7, not dismissed): {alert_ct}")
    lines.append("")
    lines.append("**Score distribution (new jobs):**")
    dist_rows = [[b, brackets.get(b, 0)] for b in bracket_order]
    lines.append(_md_table(["Bracket", "Count"], dist_rows))
    lines.append("")

    if run_summaries:
        last = run_summaries[-1]
        lines.append("**Last run stats** (from run_summary.json):")
        lines.append(f"- Status: {last.get('status', 'unknown')}")
        lines.append(f"- Timestamp: {last.get('timestamp') or last.get('runAt') or last.get('completedAt') or 'unknown'}")
        fetch_ok = last.get("descriptionFetchSuccess") or last.get("fetchSuccess") or last.get("fetchedCount")
        fetch_total = last.get("descriptionFetchTotal") or last.get("fetchTotal") or last.get("fetchAttempted")
        if fetch_ok is not None and fetch_total:
            lines.append(f"- Description fetch success rate: {_pct(fetch_ok, fetch_total)} ({fetch_ok}/{fetch_total})")
        approved = last.get("alertsApproved") or last.get("reviewApproved") or 0
        rejected = last.get("alertsRejected") or last.get("reviewRejected") or 0
        if approved or rejected:
            lines.append(f"- Alert reviews: {approved} approved, {rejected} rejected")
        lines.append("")

    return "\n".join(lines)


def section_alerts_by_lane(new_jobs: list[dict]) -> str:
    lines = ["## 3. Alerts by Search Lane (This Week)\n"]

    alerts = [j for j in new_jobs if _is_alert(j)]
    if not alerts:
        lines.append("_No new alerts this week._\n")
        return "\n".join(lines)

    by_lane: dict[str, list] = defaultdict(list)
    for j in alerts:
        by_lane[_search_lane(j)].append(j)

    rows = []
    for lane, lane_jobs in sorted(by_lane.items(), key=lambda x: -len(x[1])):
        apply_ct = sum(1 for j in lane_jobs if (j.get("reviewAction") or "").lower() in ("apply", "applied"))
        review_ct = sum(1 for j in lane_jobs if (j.get("reviewAction") or "").lower() in ("review", "review-manually", "check-salary"))
        verify_ct = sum(1 for j in lane_jobs if (j.get("reviewAction") or "").lower() in ("verify-remote", "verify remote"))
        rows.append([lane, len(lane_jobs), apply_ct, review_ct, verify_ct])

    lines.append(_md_table(["Search Lane", "Alerts", "Apply", "Review", "Verify Remote"], rows))
    lines.append("")
    return "\n".join(lines)


def section_applications(applied_data, cutoff: datetime, all_jobs: list[dict]) -> str:
    lines = ["## 4. Applications\n"]

    # Fallback: if no applied file, look in data.json itself
    entries = []
    if applied_data is not None:
        raw = applied_data if isinstance(applied_data, list) else applied_data.get("applications", [])
        for e in raw:
            if _within_window(e.get("appliedAt") or e.get("appliedDate") or e.get("date"), cutoff):
                entries.append(e)
        all_time_total = len(raw) if isinstance(applied_data, list) else len(raw)
    else:
        # Fall back to data.json applied flags
        raw = [j for j in all_jobs if j.get("applied") or j.get("appliedDate")]
        for j in raw:
            ts = j.get("appliedAt") or j.get("appliedDate")
            if ts and _within_window(ts, cutoff):
                entries.append({
                    "company": j.get("company"),
                    "title": j.get("title"),
                    "appliedAt": ts,
                    "source": j.get("src") or j.get("sourceRun") or "linkedin",
                    "confidence": j.get("score"),
                })
        all_time_total = len(raw)

    if not entries:
        lines.append("_No applications recorded this week._\n")
    else:
        rows = []
        for e in sorted(entries, key=lambda x: x.get("appliedAt") or x.get("appliedDate") or "", reverse=True):
            rows.append([
                e.get("company") or e.get("companyName") or "",
                e.get("title") or e.get("role") or "",
                (e.get("appliedAt") or e.get("appliedDate") or "")[:10],
                e.get("source") or "",
                e.get("confidence") or e.get("score") or "",
            ])
        lines.append(_md_table(["Company", "Role", "Applied At", "Source", "Confidence"], rows))
        lines.append("")

    lines.append(f"**All-time total applications:** {all_time_total}\n")
    return "\n".join(lines)


def section_starred_saved(state_data, all_jobs: list[dict]) -> str:
    lines = ["## 5. Starred / Saved\n"]

    # Build an id->job lookup
    job_map = {str(j.get("id") or ""): j for j in all_jobs}

    # Gather IDs from state file
    saved_ids: list[str] = []
    if state_data and isinstance(state_data, dict):
        jobs_map = state_data.get("jobs", {})
        for jid, entry in jobs_map.items():
            if entry.get("starred") or (entry.get("reviewStatus") or "").lower() == "saved":
                saved_ids.append(jid)

    # Also pull from data.json directly (starred field)
    inline_starred = [j for j in all_jobs if j.get("starred")]

    combined: list[dict] = list({
        str(j.get("id")): j
        for j in inline_starred
    }.values())
    for jid in saved_ids:
        if jid not in {str(j.get("id")) for j in combined}:
            if jid in job_map:
                combined.append(job_map[jid])

    if not combined:
        lines.append("_No starred or saved jobs.\n")
        return "\n".join(lines)

    combined.sort(key=lambda j: (j.get("score") or 0), reverse=True)
    rows = []
    for j in combined:
        rows.append([
            j.get("score") or "",
            j.get("title") or "",
            j.get("company") or "",
            _search_lane(j),
            j.get("reviewAction") or j.get("status") or "",
        ])
    lines.append(_md_table(["Score", "Title", "Company", "Search Lane", "Review Action"], rows))
    lines.append("")
    return "\n".join(lines)


def section_dismissal_summary(new_jobs: list[dict]) -> str:
    lines = ["## 6. Dismissal Summary (This Week)\n"]

    dismissed = [j for j in new_jobs if _is_dismissed(j)]
    if not dismissed:
        lines.append("_No dismissals recorded this week._\n")
        return "\n".join(lines)

    def _reason(j: dict) -> str:
        ra = (j.get("reviewAction") or "").lower()
        sr = (j.get("suppressionReason") or "").lower()
        if j.get("autoSuppressed"):
            return sr or "auto-suppressed"
        if ra == "dismiss":
            return "manual dismiss"
        if ra == "below-comp-floor":
            return "below comp floor"
        if ra == "ca-restricted":
            return "CA restricted"
        if j.get("caExcluded"):
            return "CA excluded"
        if (j.get("score") or 0) < 3:
            return "low score"
        if sr:
            return sr
        if j.get("dismissed"):
            return "dismissed (legacy)"
        return "other"

    reason_ct = Counter(_reason(j) for j in dismissed)
    rows = sorted(reason_ct.items(), key=lambda x: -x[1])
    lines.append(f"**Total dismissed this week:** {len(dismissed)}\n")
    lines.append(_md_table(["Reason", "Count"], rows))
    lines.append("")
    return "\n".join(lines)


def section_audit_sample(run_dir: Path) -> str:
    lines = ["## 7. Audit Sample Check\n"]

    audit_path = run_dir / "filter_audit_sample.jsonl"
    if not audit_path.exists():
        lines.append("_No audit sample file found (`run/filter_audit_sample.jsonl`)._\n")
        return "\n".join(lines)

    entries = []
    try:
        with open(audit_path) as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        entries.append(json.loads(line))
                    except json.JSONDecodeError:
                        pass
    except Exception as exc:
        log.warning("Could not read audit sample: %s", exc)
        lines.append("_Could not read audit sample file._\n")
        return "\n".join(lines)

    if not entries:
        lines.append("_Audit sample file is empty._\n")
        return "\n".join(lines)

    lines.append(
        "Jobs the metadata filter did NOT select for description fetch "
        "(false-negative audit — these might have been relevant):\n"
    )
    rows = []
    for e in entries[:50]:
        rows.append([
            e.get("score") or e.get("metadataScore") or "",
            e.get("title") or "",
            e.get("company") or "",
            (e.get("location") or e.get("locationRaw") or "")[:30],
            e.get("filterReason") or e.get("reason") or "",
        ])
    lines.append(_md_table(["Score", "Title", "Company", "Location", "Filter Reason"], rows))
    lines.append("")
    return "\n".join(lines)


def section_quality_signals(new_jobs: list[dict]) -> str:
    lines = ["## 8. Quality Signals\n"]

    if not new_jobs:
        lines.append("_No new jobs to report quality signals for._\n")
        return "\n".join(lines)

    remote_jobs = [j for j in new_jobs if _is_remote(j)]
    ca_confirmed = sum(1 for j in remote_jobs if j.get("caEligible") is True)
    with_salary = sum(1 for j in new_jobs if _salary_display(j))
    with_lane = sum(1 for j in new_jobs if j.get("searchLane") or j.get("category"))
    with_desc = [j for j in new_jobs if j.get("description") or j.get("descriptionFetched")]
    scored_desc = [j for j in with_desc if j.get("score") is not None]
    avg_score = (
        round(sum(float(j["score"]) for j in scored_desc) / len(scored_desc), 1)
        if scored_desc else None
    )

    rows = [
        ["Remote jobs with CA eligibility confirmed", f"{ca_confirmed}/{len(remote_jobs)}", _pct(ca_confirmed, len(remote_jobs))],
        ["Jobs with salary data", f"{with_salary}/{len(new_jobs)}", _pct(with_salary, len(new_jobs))],
        ["Jobs with search lane assigned", f"{with_lane}/{len(new_jobs)}", _pct(with_lane, len(new_jobs))],
        ["Jobs with descriptions fetched", f"{len(with_desc)}/{len(new_jobs)}", _pct(len(with_desc), len(new_jobs))],
        ["Average score (described jobs)", avg_score if avg_score is not None else "n/a", ""],
    ]
    lines.append(_md_table(["Signal", "Count", "Rate"], rows))
    lines.append("")
    return "\n".join(lines)


def section_top_roles(new_jobs: list[dict]) -> str:
    lines = ["## 9. Top Roles This Week\n"]

    top = [j for j in new_jobs if (j.get("score") or 0) >= 8 and not _is_dismissed(j)]
    top.sort(key=lambda j: (j.get("score") or 0), reverse=True)

    if not top:
        lines.append("_No high-score roles this week._\n")
        return "\n".join(lines)

    rows = []
    for j in top[:15]:
        url = j.get("linkedinUrl") or j.get("link") or j.get("applyUrl") or ""
        rows.append([
            j.get("score") or "",
            j.get("title") or "",
            j.get("company") or "",
            (j.get("locationRaw") or j.get("location") or "")[:25],
            _salary_display(j)[:20],
            j.get("reviewAction") or j.get("status") or "",
            url[:60],
        ])
    lines.append(_md_table(["Score", "Title", "Company", "Location", "Salary", "Review Action", "URL"], rows))
    lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Run summary loader
# ---------------------------------------------------------------------------

def _load_run_summaries(run_dir: Path) -> list[dict]:
    """Load all run_summary.json files in run_dir, sorted by timestamp."""
    summaries = []
    if not run_dir.exists():
        log.warning("Run summaries directory not found: %s", run_dir)
        return summaries
    for p in sorted(run_dir.glob("**/run_summary.json")):
        data = _load_json(p, "run_summary")
        if data:
            summaries.append(data)
    # Also try a single run_summary.json at the root of run_dir
    root_summary = run_dir / "run_summary.json"
    if root_summary.exists() and root_summary not in [run_dir / "run_summary.json"]:
        data = _load_json(root_summary, "run_summary")
        if data:
            summaries.append(data)
    # Sort by any timestamp field
    def _ts_key(s):
        for k in ("timestamp", "runAt", "completedAt", "startedAt"):
            if s.get(k):
                return s[k]
        return ""
    summaries.sort(key=_ts_key)
    return summaries


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def build_report(
    data_path: Path,
    applied_path: Path,
    state_path: Path,
    run_dir: Path,
    days: int,
) -> str:
    today = datetime.now(timezone.utc).date()
    generated = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)

    # Load data sources
    data = _load_json(data_path, "data.json")
    applied_data = _load_json(applied_path, "applied")
    state_data = _load_json(state_path, "state")
    run_summaries = _load_run_summaries(run_dir)

    all_jobs = _get_jobs_list(data)

    # Filter to new jobs within the window
    new_jobs = [j for j in all_jobs if _within_window(_job_timestamp(j), cutoff)]
    log.info("Total jobs in data.json: %d", len(all_jobs))
    log.info("Jobs within past %d days: %d", days, len(new_jobs))

    sections = [
        section_header(str(today), generated),
        "",
        "---",
        "",
        section_pipeline_summary(new_jobs, run_summaries),
        section_alerts_by_lane(new_jobs),
        section_applications(applied_data, cutoff, all_jobs),
        section_starred_saved(state_data, all_jobs),
        section_dismissal_summary(new_jobs),
        section_audit_sample(run_dir),
        section_quality_signals(new_jobs),
        section_top_roles(new_jobs),
    ]

    return "\n".join(sections)


def main():
    parser = argparse.ArgumentParser(
        description="Generate a Job Scout weekly metrics report."
    )
    parser.add_argument(
        "--data",
        type=Path,
        default=SCRIPT_DIR / "data.json",
        help="Path to data.json (default: data.json next to this script)",
    )
    parser.add_argument(
        "--applied",
        type=Path,
        default=SCRIPT_DIR / "job-scout-applied.json",
        help="Path to job-scout-applied.json",
    )
    parser.add_argument(
        "--state",
        type=Path,
        default=SCRIPT_DIR / "job-scout-state.json",
        help="Path to job-scout-state.json",
    )
    parser.add_argument(
        "--run-summaries",
        type=Path,
        default=SCRIPT_DIR / "run",
        dest="run_summaries",
        help="Directory containing run_summary.json file(s)",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=None,
        help="Output path for the report (default: reports/jobscout_weekly_YYYY-MM-DD.md)",
    )
    parser.add_argument(
        "--days",
        type=int,
        default=7,
        help="Look-back window in days (default: 7)",
    )
    args = parser.parse_args()

    # Resolve default output path
    today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    if args.out is None:
        reports_dir = SCRIPT_DIR / "reports"
        args.out = reports_dir / f"jobscout_weekly_{today_str}.md"

    # Ensure output directory exists
    args.out.parent.mkdir(parents=True, exist_ok=True)

    report = build_report(
        data_path=args.data,
        applied_path=args.applied,
        state_path=args.state,
        run_dir=args.run_summaries,
        days=args.days,
    )

    with open(args.out, "w") as f:
        f.write(report)

    log.info("Report written to: %s", args.out)
    print(f"Report written to: {args.out}")


if __name__ == "__main__":
    main()
