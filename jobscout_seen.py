"""jobscout_seen.py — Deterministic seen-ID manager.

Replaces the prompt-driven seen-ID update step with auditable Python code.

What it does:
  1. Validates at least one scrape file from the current run has content.
  2. Reads the existing seen-IDs file (creates it if absent).
  3. Collects IDs scraped this run from run/scan_*.jsonl.
  4. Adds new IDs with an ISO timestamp.
  5. Enforces retention: IDs present in data.json are always kept; others older
     than minimumRetentionDays are eligible for pruning.
  6. Enforces maxIds cap by removing oldest eligible entries first.
  7. Writes the updated seen-IDs file back.
  8. Optionally archives a run summary JSON to run_summaries/.

File format (backwards-compatible with jobscout_prep.py):
  {
    "ids":        ["id1", "id2", ...],        <- read by jobscout_prep.py
    "timestamps": {"id1": "2026-01-01T…", …} <- managed by this script
  }

Usage:
  python3 jobscout_seen.py \\
      --seen    ../job-scout-seen.json \\
      --data    data.json \\
      --config  config/jobscout_config.json \\
      --run-dir run \\
      [--run-summary-out run/run_summary.json]
"""
import argparse
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path


# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------
SCRIPT_DIR = Path(__file__).parent.resolve()

DEFAULT_SEEN    = SCRIPT_DIR / "job-scout-seen.json"
DEFAULT_DATA    = SCRIPT_DIR / "data.json"
DEFAULT_CONFIG  = SCRIPT_DIR / "config" / "jobscout_config.json"
DEFAULT_RUN_DIR = SCRIPT_DIR / "run"

DEFAULT_MAX_IDS        = 25_000
DEFAULT_RETENTION_DAYS = 90


# ---------------------------------------------------------------------------
# I/O helpers
# ---------------------------------------------------------------------------

def _load_json(path: Path, default):
    if not path.exists():
        return default
    try:
        with path.open() as fh:
            return json.load(fh)
    except (json.JSONDecodeError, OSError) as exc:
        print(f"WARNING: could not read {path}: {exc}", file=sys.stderr)
        return default


def _read_jsonl_ids(path: Path) -> list[str]:
    """Return all non-empty job IDs from a .jsonl file."""
    ids = []
    if not path.exists():
        return ids
    with path.open() as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            jid = str(obj.get("id") or "").strip()
            if jid and jid != "None":
                ids.append(jid)
    return ids


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

def load_config(path: Path) -> dict:
    raw = _load_json(path, {})
    seen_cfg = raw.get("seenIds", {})
    return {
        "max_ids":        int(seen_cfg.get("maxIds",              DEFAULT_MAX_IDS)),
        "retention_days": int(seen_cfg.get("minimumRetentionDays", DEFAULT_RETENTION_DAYS)),
    }


# ---------------------------------------------------------------------------
# Seen-IDs file
# ---------------------------------------------------------------------------

def load_seen(path: Path) -> tuple[list[str], dict[str, str]]:
    """Return (ids_list, timestamps_dict).

    ids_list        — ordered list of ID strings (insertion order preserved)
    timestamps_dict — {id: iso_string}; may be empty for legacy files
    """
    raw = _load_json(path, {})
    if isinstance(raw, list):
        # Plain-list legacy format
        ids = [str(i) for i in raw]
        return ids, {}
    if isinstance(raw, dict):
        ids = [str(i) for i in raw.get("ids", [])]
        ts  = {str(k): str(v) for k, v in raw.get("timestamps", {}).items()}
        return ids, ts
    return [], {}


def save_seen(path: Path, ids: list[str], timestamps: dict[str, str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"ids": ids, "timestamps": timestamps}
    tmp = path.with_suffix(".tmp")
    with tmp.open("w") as fh:
        json.dump(payload, fh, indent=1)
    tmp.replace(path)


# ---------------------------------------------------------------------------
# Scrape validation
# ---------------------------------------------------------------------------

def validate_scrape(run_dir: Path) -> dict[str, int]:
    """Return counts per scan file.  Exits with code 2 if ALL files are empty/absent."""
    scan_files = list(run_dir.glob("scan_*.jsonl"))
    counts = {}
    for sf in scan_files:
        counts[sf.name] = len(_read_jsonl_ids(sf))

    if not counts or all(v == 0 for v in counts.values()):
        print(
            "ERROR: No scrape output found in run/ — "
            "aborting seen-ID update to avoid false positives.",
            file=sys.stderr,
        )
        sys.exit(2)

    return counts


# ---------------------------------------------------------------------------
# Core logic
# ---------------------------------------------------------------------------

def collect_run_ids(run_dir: Path) -> list[str]:
    """Collect all job IDs scraped in the current run (deduped, order-preserved)."""
    seen: set[str] = set()
    result: list[str] = []
    for sf in sorted(run_dir.glob("scan_*.jsonl")):
        for jid in _read_jsonl_ids(sf):
            if jid not in seen:
                seen.add(jid)
                result.append(jid)
    return result


def load_data_ids(data_path: Path) -> set[str]:
    """Return the set of all IDs currently in data.json (for retention protection)."""
    raw = _load_json(data_path, {})
    if isinstance(raw, dict):
        jobs = raw.get("jobs", [])
    elif isinstance(raw, list):
        jobs = raw
    else:
        jobs = []
    return {str(j.get("id", "")).strip() for j in jobs if j.get("id")}


def prune(
    ids: list[str],
    timestamps: dict[str, str],
    data_ids: set[str],
    max_ids: int,
    retention_days: int,
    now: datetime,
) -> tuple[list[str], dict[str, str]]:
    """Remove entries that are:
      - older than retention_days
      - AND not in data.json (active pipeline records are always kept)

    Then enforce max_ids by dropping the oldest remaining entries first.
    """
    cutoff = now - timedelta(days=retention_days)

    def _added_at(jid: str) -> datetime:
        ts_str = timestamps.get(jid, "")
        if ts_str:
            try:
                return datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
            except ValueError:
                pass
        # No timestamp → treat as very old so it's eligible for pruning
        # (but still protected if in data.json)
        return datetime.min.replace(tzinfo=timezone.utc)

    # Step 1: retention prune — remove old IDs not in current pipeline
    retained = [
        jid for jid in ids
        if jid in data_ids or _added_at(jid) >= cutoff
    ]

    # Step 2: cap — if still over limit, drop oldest non-protected entries first
    if len(retained) > max_ids:
        protected = {jid for jid in retained if jid in data_ids}
        evictable = [jid for jid in retained if jid not in protected]
        # Sort evictable oldest-first
        evictable.sort(key=_added_at)
        overflow = len(retained) - max_ids
        evictable = evictable[overflow:]
        # Rebuild in original order
        evict_set = set(retained) - set(evictable) - protected
        retained = [jid for jid in retained if jid not in evict_set]

    new_ts = {jid: timestamps[jid] for jid in retained if jid in timestamps}
    return retained, new_ts


def update_seen(
    ids: list[str],
    timestamps: dict[str, str],
    run_ids: list[str],
    now: datetime,
) -> tuple[list[str], dict[str, str], int]:
    """Add new run IDs (with current timestamp).  Returns (new_ids, new_ts, added_count)."""
    existing = set(ids)
    new_ts = dict(timestamps)
    new_ids = list(ids)
    added = 0
    now_iso = now.isoformat()

    for jid in run_ids:
        if jid not in existing:
            new_ids.append(jid)
            new_ts[jid] = now_iso
            existing.add(jid)
            added += 1

    return new_ids, new_ts, added


# ---------------------------------------------------------------------------
# Run summary
# ---------------------------------------------------------------------------

def write_run_summary(
    out_path: Path,
    scan_counts: dict[str, int],
    added: int,
    pruned: int,
    total_after: int,
    now: datetime,
) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "generatedAt":  now.isoformat(),
        "scrapecounts":  scan_counts,
        "idsAdded":     added,
        "idsPruned":    pruned,
        "totalSeenIds": total_after,
    }
    with out_path.open("w") as fh:
        json.dump(payload, fh, indent=2)
    print(f"Run summary written → {out_path}")


def archive_run_summary(data_path: Path, summary_path: Path, now: datetime) -> None:
    """Copy run_summary.json into run_summaries/ with a datestamped filename."""
    if not summary_path.exists():
        return
    archive_dir = data_path.parent / "run_summaries"
    archive_dir.mkdir(parents=True, exist_ok=True)
    stamp = now.strftime("%Y-%m-%d_%H%M%S")
    dest = archive_dir / f"run_summary_{stamp}.json"
    import shutil
    shutil.copy2(summary_path, dest)
    print(f"Run summary archived → {dest}")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(
        description="Manage Job Scout seen-IDs deterministically."
    )
    parser.add_argument("--seen",   type=Path, default=DEFAULT_SEEN,
                        help="Path to job-scout-seen.json (default: ../job-scout-seen.json)")
    parser.add_argument("--data",   type=Path, default=DEFAULT_DATA,
                        help="Path to data.json")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG,
                        help="Path to jobscout_config.json")
    parser.add_argument("--run-dir", type=Path, default=DEFAULT_RUN_DIR,
                        help="Directory containing scan_*.jsonl files")
    parser.add_argument("--run-summary-out", type=Path,
                        default=DEFAULT_RUN_DIR / "run_summary.json",
                        help="Where to write run_summary.json (also archived to run_summaries/)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Print what would change without writing anything")
    args = parser.parse_args()

    now = datetime.now(timezone.utc)
    cfg = load_config(args.config)

    # 1. Validate scrape
    scan_counts = validate_scrape(args.run_dir)
    total_scraped = sum(scan_counts.values())
    print(f"[seen] Scrape validated: {total_scraped} total IDs across {len(scan_counts)} file(s).")

    # 2. Load state
    ids, timestamps = load_seen(args.seen)
    data_ids        = load_data_ids(args.data)
    run_ids         = collect_run_ids(args.run_dir)

    print(f"[seen] Current seen-IDs: {len(ids)}")
    print(f"[seen] Active data.json IDs (retention-protected): {len(data_ids)}")
    print(f"[seen] IDs from current run: {len(run_ids)}")

    # 3. Add new IDs
    ids, timestamps, added = update_seen(ids, timestamps, run_ids, now)
    print(f"[seen] Added: {added} new IDs")

    # 4. Prune (retention + cap)
    before_prune = len(ids)
    ids, timestamps = prune(
        ids, timestamps, data_ids,
        cfg["max_ids"], cfg["retention_days"], now,
    )
    pruned = before_prune - len(ids)
    if pruned:
        print(f"[seen] Pruned: {pruned} expired IDs (>{cfg['retention_days']}d, not in data.json)")

    print(f"[seen] Total seen-IDs after update: {len(ids)} (cap: {cfg['max_ids']})")

    # Safety check: refuse to write if the seen-IDs count dropped dramatically.
    # A large unexpected drop (>20% AND >100 IDs) suggests data.json was already
    # truncated by the build step, causing cascading loss of retention protection.
    ids_before_this_run = before_prune - added  # count before we added new IDs this run
    if ids_before_this_run > 0 and pruned > 100:
        drop_pct = pruned / ids_before_this_run
        if drop_pct > 0.20:
            msg = (
                f"SAFETY ABORT: seen-IDs would drop by {pruned} ({drop_pct:.0%} of {ids_before_this_run}) "
                f"— this likely means data.json was truncated before this step ran. "
                f"Refusing to write seen-IDs to avoid cascading data loss. "
                f"Check data.json job count and re-run after verifying."
            )
            print(msg, file=sys.stderr)
            sys.exit(3)

    # 5. Write
    if args.dry_run:
        print("[seen] DRY RUN — no files written.")
    else:
        save_seen(args.seen, ids, timestamps)
        print(f"[seen] Written → {args.seen}")

        # 6. Run summary
        write_run_summary(
            args.run_summary_out, scan_counts, added, pruned, len(ids), now
        )
        archive_run_summary(args.data, args.run_summary_out, now)

    return 0


if __name__ == "__main__":
    sys.exit(main())
