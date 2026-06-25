"""jobscout_alerts.py — Wave 3, WP-M

Generate Slack alert candidates from fully-normalized, post-build state.
Replaces inline alert logic from SKILL.md Step 12.

Usage:
    python3 jobscout_alerts.py \
        --data data.json \
        --state job-scout-state.json \
        --applied job-scout-applied.json \
        --config config/jobscout_config.json \
        --out run/alert_candidates.json
"""
import argparse
import json
import os
import re
import sys
from datetime import datetime, timezone


# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

DEFAULT_DATA    = os.path.join(SCRIPT_DIR, "data.json")
DEFAULT_STATE   = os.path.join(SCRIPT_DIR, "job-scout-state.json")
DEFAULT_APPLIED = os.path.join(SCRIPT_DIR, "job-scout-applied.json")
DEFAULT_CONFIG  = os.path.join(SCRIPT_DIR, "config", "jobscout_config.json")
DEFAULT_OUT     = os.path.join(SCRIPT_DIR, "run", "alert_candidates.json")

# Hardcoded fallback thresholds (used when config is missing)
DEFAULT_MIN_SCORE        = 7
DEFAULT_FLOOR_REMOTE     = 120000
DEFAULT_FLOOR_OC         = 120000
DEFAULT_COMMUTE_MAX_MIN  = 45


# ---------------------------------------------------------------------------
# I/O helpers
# ---------------------------------------------------------------------------

def load_json_file(path, default, label):
    """Load a JSON file. Return default and log a warning if missing/invalid."""
    try:
        with open(path) as f:
            return json.load(f)
    except FileNotFoundError:
        print(f"WARNING: {label} not found at {path!r} — {_default_msg(default)}", file=sys.stderr)
        return default
    except json.JSONDecodeError as e:
        print(f"WARNING: {label} at {path!r} is not valid JSON ({e}) — {_default_msg(default)}", file=sys.stderr)
        return default


def _default_msg(default):
    if isinstance(default, dict) and not default:
        return "using empty dict"
    if isinstance(default, list) and not default:
        return "using empty list"
    return "using defaults"


def load_config(path):
    """Load config. Returns dict of resolved thresholds."""
    raw = load_json_file(path, {}, "config")
    alerts = raw.get("alerts", {})
    comp   = raw.get("compensation", {})
    geo    = raw.get("geo", {})
    return {
        "min_score":     alerts.get("minimumScore",          DEFAULT_MIN_SCORE),
        "floor_remote":  comp.get("baseSalaryFloorRemote",   DEFAULT_FLOOR_REMOTE),
        "floor_oc":      comp.get("baseSalaryFloorOC",       DEFAULT_FLOOR_OC),
        "commute_max":   geo.get("commuteMaxMinutes",        DEFAULT_COMMUTE_MAX_MIN),
        "max_age_hours": alerts.get("maxAgeHours",           36),
    }


def load_state(path):
    """Load job-scout-state.json. Returns {"jobs": {}} if missing."""
    data = load_json_file(path, {"jobs": {}}, "state file")
    if not isinstance(data, dict) or "jobs" not in data:
        return {"jobs": {}}
    return data


def load_applied(path):
    """Load job-scout-applied.json. Handles both list and {"appliedRoles": [...]} shapes."""
    data = load_json_file(path, [], "applied list")
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        return data.get("appliedRoles", data.get("applied", []))
    return []


def load_data(path):
    """Load data.json. Exits with code 1 if missing."""
    if not os.path.exists(path):
        print(f"ERROR: data.json not found at {path!r}", file=sys.stderr)
        sys.exit(1)
    try:
        with open(path) as f:
            data = json.load(f)
        if isinstance(data, list):
            return data
        # Some pipeline outputs wrap in {"jobs": [...]}
        if isinstance(data, dict):
            for key in ("jobs", "results", "data"):
                if isinstance(data.get(key), list):
                    return data[key]
        print(f"ERROR: Unexpected structure in data.json (expected list or dict with jobs/results/data key)", file=sys.stderr)
        sys.exit(1)
    except json.JSONDecodeError as e:
        print(f"ERROR: data.json is not valid JSON: {e}", file=sys.stderr)
        sys.exit(1)


# ---------------------------------------------------------------------------
# Applied list matching
# ---------------------------------------------------------------------------

_STRIP_SUFFIXES = re.compile(r"\b(inc|llc|corp|ltd|co)\b\.?$", re.I)
_PUNCT = re.compile(r"[^\w\s]")


def normalize_company(name):
    """Lowercase, strip punctuation, strip common suffixes."""
    if not name:
        return ""
    s = name.lower().strip()
    s = _PUNCT.sub("", s)
    s = _STRIP_SUFFIXES.sub("", s).strip()
    return s


def _normalize_for_match(s: str) -> str:
    """Lowercase, strip punctuation, remove common legal suffixes."""
    s = s.lower()
    s = re.sub(r"[^\w\s]", "", s)
    for suffix in (" inc", " llc", " corp", " ltd", " co"):
        s = s.rstrip(suffix)
    return s.strip()


def build_applied_set(applied_list):
    """Return a set of normalized company names from the applied list."""
    seen = set()
    for entry in applied_list:
        if not isinstance(entry, dict):
            continue
        company = entry.get("company") or entry.get("companyName") or ""
        norm = normalize_company(company)
        if norm:
            seen.add(norm)
    return seen


_UNKNOWN_ROLE = re.compile(r"^unknown", re.I)


def is_applied(job: dict, applied_list: list) -> bool:
    """Return True only if company AND role both match an applied entry."""
    if job.get("applied") is True:
        return True
    # Also respect reviewStatus set via the dashboard UI
    review_status = job.get("reviewStatus") or ""
    if review_status and review_status not in ("new", "dismissed", ""):
        return True
    company = _normalize_for_match(job.get("companyName") or job.get("company") or "")
    title   = _normalize_for_match(job.get("title") or "")
    if not company:
        return False
    for entry in applied_list:
        if not isinstance(entry, dict):
            continue
        entry_company = _normalize_for_match(entry.get("company") or "")
        raw_role      = entry.get("role") or ""
        # Treat blank or "Unknown*" roles as wildcards (match any title)
        role_is_wildcard = not raw_role or bool(_UNKNOWN_ROLE.match(raw_role.strip()))
        entry_role    = "" if role_is_wildcard else _normalize_for_match(raw_role)
        # Require company match always; role match if role is populated
        if entry_company and (entry_company in company or company in entry_company):
            if not entry_role or entry_role in title or title in entry_role:
                return True
    return False


# ---------------------------------------------------------------------------
# Salary display
# ---------------------------------------------------------------------------

def fmt_k(value):
    """Convert 143000 → '$143K'."""
    k = round(value / 1000)
    return f"${k}K"


def salary_display(job):
    """Format salary as '$143K–$190K', '~$143K', or 'Unknown'."""
    lo = job.get("salaryLow")
    hi = job.get("salaryHigh")
    if lo is None and hi is None:
        return "Unknown"
    if lo is not None and hi is not None:
        if lo == hi:
            return f"~{fmt_k(lo)}"
        return f"{fmt_k(min(lo, hi))}–{fmt_k(max(lo, hi))}"
    if hi is not None:
        return f"~{fmt_k(hi)}"
    return f"~{fmt_k(lo)}"


# ---------------------------------------------------------------------------
# Risk flags
# ---------------------------------------------------------------------------

def build_risk_flags(job):
    flags = []

    # Salary unknown
    if job.get("salaryHigh") is None and job.get("salaryLow") is None:
        flags.append("Salary unknown")

    # Top of range only
    if job.get("compRisk") == "top-of-range-only":
        flags.append("Top of range only")

    # CA eligibility unconfirmed (None means unknown — False is excluded upstream)
    if job.get("caEligible") is None:
        flags.append("CA eligibility unconfirmed")

    # Hybrid/On-site commute warning
    workplace = job.get("workplaceNormalized") or ""
    commute = job.get("commuteEstimateMinutes")
    if workplace in ("Hybrid", "On-site") and commute is not None:
        flags.append(f"HYBRID/ONSITE: estimated commute ~{commute}min — confirm before applying")

    # Contract — verify benefits
    if job.get("compRisk") == "contract-verify":
        flags.append("Contract — verify benefits")

    return flags


# ---------------------------------------------------------------------------
# Review action
# ---------------------------------------------------------------------------

def review_action(job, risk_flags):
    score = _get_score(job)
    geo_fit = job.get("geoFit")
    ca_eligible = job.get("caEligible")

    if risk_flags:
        return f"Review — {risk_flags[0]}"
    if score >= 8 and geo_fit is True and ca_eligible is True:
        return "Apply"
    if geo_fit is None:
        return "Verify remote"
    return "Review"


# ---------------------------------------------------------------------------
# Score helper — handle both "score" and "_score"
# ---------------------------------------------------------------------------

def _get_score(job):
    val = job.get("score") if job.get("score") is not None else job.get("_score")
    if val is None:
        return 0
    try:
        return float(val)
    except (TypeError, ValueError):
        return 0


# ---------------------------------------------------------------------------
# Alert selection
# ---------------------------------------------------------------------------

def is_alert_candidate(job, cfg, state_jobs, applied_list):
    """Return (True, notes) or (False, reason)."""

    # 0. Cross-post duplicate — description fingerprint matched a better posting
    if job.get("dupeOf"):
        return False, f"cross-post dupe of {job['dupeOf']}"

    # 1. Score >= min_score
    score = _get_score(job)
    if score < cfg["min_score"]:
        return False, f"score {score} < {cfg['min_score']}"

    # 2. geoFit is True or None (False → excluded)
    geo_fit = job.get("geoFit")
    if geo_fit is False:
        return False, "geoFit is False"

    # 3. caExcluded is not True
    if job.get("caExcluded") is True:
        return False, "caExcluded"

    # 3b. caEligible explicitly False (state-restricted role that excludes CA)
    if job.get("caEligible") is False:
        return False, "caEligible is False"

    # 4. Not already applied (field + applied list)
    if is_applied(job, applied_list):
        return False, "already applied"

    # 5. userDismissed — check state overlay first, then job field
    job_id = str(job.get("id", ""))
    state_entry = state_jobs.get(job_id, {})
    if state_entry.get("userDismissed") or job.get("userDismissed") is True:
        return False, "userDismissed"

    # 5b. Already alerted — suppressed to prevent re-alerting across runs
    if state_entry.get("alertedAt"):
        return False, f"already alerted ({str(state_entry['alertedAt'])[:10]})"

    # 5c. Recency gate — only alert jobs added in the last max_age_hours
    #     Prevents stale data.json entries from surfacing as new alerts.
    max_age_hours = cfg.get("max_age_hours", 36)
    added_at = job.get("addedAt")
    if added_at:
        try:
            added_dt = datetime.fromisoformat(added_at.replace("Z", "+00:00"))
            age_hours = (datetime.now(timezone.utc) - added_dt).total_seconds() / 3600
            if age_hours > max_age_hours:
                return False, f"too old (addedAt {age_hours:.0f}h ago)"
        except (ValueError, TypeError):
            pass  # unparseable addedAt → don't filter

    # 6. Salary floor check
    salary_high = job.get("salaryHigh")
    source_run  = (job.get("sourceRun") or "").lower()
    workplace   = (job.get("workplaceNormalized") or "").lower()
    is_remote   = source_run == "remote" or "remote" in workplace
    floor       = cfg["floor_remote"] if is_remote else cfg["floor_oc"]

    if salary_high is not None:
        try:
            if float(salary_high) < floor:
                return False, f"salaryHigh {salary_high} < floor {floor}"
        except (TypeError, ValueError):
            pass  # treat unparseable as unknown → allow

    # 7. Commute <= max OR None (unknown)
    commute = job.get("commuteEstimateMinutes")
    if commute is not None:
        try:
            if float(commute) > cfg["commute_max"]:
                return False, f"commute {commute}min > {cfg['commute_max']}min"
        except (TypeError, ValueError):
            pass

    return True, None


# ---------------------------------------------------------------------------
# Candidate builder
# ---------------------------------------------------------------------------

def build_candidate(job, state_jobs):
    """Build the alert candidate dict for a qualifying job."""
    job_id = str(job.get("id", ""))
    state_entry = state_jobs.get(job_id, {})

    risk_flags = build_risk_flags(job)
    action     = review_action(job, risk_flags)

    # geoFit risk note
    extra_flags = list(risk_flags)
    if job.get("geoFit") is None:
        if "CA eligibility unconfirmed" not in extra_flags:
            extra_flags.append("CA eligibility unconfirmed")

    linked_in_url = (
        job.get("linkedInUrl")
        or job.get("linkedinUrl")
        or job.get("url")
        or job.get("applyUrl")
        or job.get("link")
    )

    return {
        "id":                  job.get("id"),
        "score":               _get_score(job),
        "title":               job.get("title"),
        "companyName":         job.get("companyName") or job.get("company"),
        "location":            job.get("locationRaw") or job.get("location"),
        "workplaceNormalized": job.get("workplaceNormalized"),
        "salaryDisplay":       salary_display(job),
        "searchLane":          job.get("searchLane"),
        "scoreReason":         job.get("scoreReason"),
        "reviewAction":        action,
        "riskFlags":           extra_flags,
        "linkedInUrl":         linked_in_url,
        "alertApproved":       state_entry.get("alertApproved"),
        "alertRejectionReason": state_entry.get("alertRejectionReason"),
    }


# ---------------------------------------------------------------------------
# Summary printer
# ---------------------------------------------------------------------------

def print_summary(candidates):
    action_counts = {}
    for c in candidates:
        action = c.get("reviewAction") or "Unknown"
        # Bucket into broad categories for the summary
        if action.startswith("Apply"):
            key = "Apply"
        elif action.startswith("Verify remote"):
            key = "Verify remote"
        else:
            key = "Review"
        action_counts[key] = action_counts.get(key, 0) + 1

    print(f"Alert candidates: {len(candidates)}")
    for label in ("Apply", "Review", "Verify remote"):
        count = action_counts.get(label, 0)
        if count:
            print(f"  {label}: {count}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Generate Slack alert candidates from post-build job data."
    )
    parser.add_argument("--data",    default=DEFAULT_DATA,    help="Path to data.json")
    parser.add_argument("--state",   default=DEFAULT_STATE,   help="Path to job-scout-state.json")
    parser.add_argument("--applied", default=DEFAULT_APPLIED, help="Path to job-scout-applied.json")
    parser.add_argument("--config",  default=DEFAULT_CONFIG,  help="Path to jobscout_config.json")
    parser.add_argument("--out",     default=DEFAULT_OUT,     help="Output path for alert_candidates.json")
    args = parser.parse_args()

    # Load inputs
    jobs         = load_data(args.data)          # exits on failure
    state        = load_state(args.state)
    applied_list = load_applied(args.applied)
    cfg          = load_config(args.config)

    state_jobs   = state.get("jobs", {})

    # Select and build candidates
    candidates = []
    skipped    = 0

    for raw_job in jobs:
        if not isinstance(raw_job, dict):
            skipped += 1
            continue
        try:
            ok, reason = is_alert_candidate(raw_job, cfg, state_jobs, applied_list)
            if ok:
                candidates.append(build_candidate(raw_job, state_jobs))
        except Exception as e:
            job_id = raw_job.get("id", "?")
            print(f"WARNING: skipping job {job_id!r} due to error: {e}", file=sys.stderr)
            skipped += 1

    if skipped:
        print(f"WARNING: skipped {skipped} malformed job record(s)", file=sys.stderr)

    # Sort by score descending
    candidates.sort(key=lambda c: c.get("score") or 0, reverse=True)

    # Write output
    out_dir = os.path.dirname(args.out)
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)

    output = {
        "generatedAt":      datetime.now(timezone.utc).isoformat(),
        "totalCandidates":  len(candidates),
        "candidates":       candidates,
    }

    with open(args.out, "w") as f:
        json.dump(output, f, indent=2)

    print_summary(candidates)

    return 0


if __name__ == "__main__":
    sys.exit(main())
