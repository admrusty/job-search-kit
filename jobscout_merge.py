"""Job Scout — merge script.

Combines all scored output files from a pipeline run into a single scored.json:
  - run/bulk_low.json        (score 1–2, title-scored, no description)
  - run/bulk_3.json          (score 3, description saved, no LLM)
  - run/chunks/scored_chunk_*.json  (LLM-scored, one file per chunk)

Deduplicates by job ID (first occurrence wins).
Warns to stderr if any scored chunk is missing _score or _reason fields.

Usage:
  python3 jobscout_merge.py --run-dir run --output run/scored.json
"""

import argparse
import json
import sys
from pathlib import Path


def load_json_list(path: Path, label: str) -> list[dict]:
    """Load a JSON file expected to contain a list of objects.

    Returns an empty list if the file is missing or empty.
    """
    if not path.exists():
        print(f"[merge] WARNING: {label} not found at {path} — skipping.", file=sys.stderr)
        return []
    with path.open() as fh:
        try:
            data = json.load(fh)
        except json.JSONDecodeError as exc:
            print(f"[merge] ERROR: Could not parse {path}: {exc}", file=sys.stderr)
            return []
    if not isinstance(data, list):
        print(
            f"[merge] WARNING: {path} is not a JSON list (got {type(data).__name__}) — skipping.",
            file=sys.stderr,
        )
        return []
    return data


def validate_scored_chunk(jobs: list[dict], chunk_path: Path) -> None:
    """Warn if any job in a scored chunk is missing _score or _reason."""
    missing = [
        j.get("id", "<no-id>")
        for j in jobs
        if "_score" not in j or "_reason" not in j
    ]
    if missing:
        print(
            f"[merge] WARNING: {chunk_path.name} has {len(missing)} job(s) "
            f"missing _score/_reason: {missing[:5]}{'...' if len(missing) > 5 else ''}",
            file=sys.stderr,
        )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Merge bulk and LLM-scored job files into a single scored.json."
    )
    parser.add_argument(
        "--run-dir",
        default="run",
        help="Directory containing bulk_low.json, bulk_3.json, and chunks/ (default: run).",
    )
    parser.add_argument(
        "--output",
        default="run/scored.json",
        help="Output path for merged scored JSON (default: run/scored.json).",
    )
    args = parser.parse_args()

    run_dir = Path(args.run_dir)
    output_path = Path(args.output)

    # -----------------------------------------------------------------------
    # Load source files
    # -----------------------------------------------------------------------
    bulk_low = load_json_list(run_dir / "bulk_low.json", "bulk_low")
    print(f"[merge] bulk_low:   {len(bulk_low)} jobs")

    bulk_3 = load_json_list(run_dir / "bulk_3.json", "bulk_3")
    print(f"[merge] bulk_3:     {len(bulk_3)} jobs")

    # Discover all scored chunk files in sorted order.
    chunks_dir = run_dir / "chunks"
    chunk_files = sorted(chunks_dir.glob("scored_chunk_*.json")) if chunks_dir.exists() else []
    if not chunk_files:
        print("[merge] No scored_chunk_*.json files found in chunks/.", file=sys.stderr)

    chunk_jobs_total = 0
    all_chunk_jobs: list[dict] = []
    for chunk_path in chunk_files:
        jobs = load_json_list(chunk_path, chunk_path.name)
        validate_scored_chunk(jobs, chunk_path)
        all_chunk_jobs.extend(jobs)
        chunk_jobs_total += len(jobs)
        print(f"[merge] {chunk_path.name}: {len(jobs)} jobs")

    print(f"[merge] chunks total: {chunk_jobs_total} jobs across {len(chunk_files)} file(s)")

    # -----------------------------------------------------------------------
    # Merge and deduplicate (first occurrence wins)
    # -----------------------------------------------------------------------
    merged: list[dict] = []
    seen_ids: set[str] = set()
    duplicate_count = 0

    for source_jobs in (bulk_low, bulk_3, all_chunk_jobs):
        for job in source_jobs:
            job_id = str(job.get("id", ""))
            if not job_id:
                # Include jobs without an ID rather than silently dropping them.
                merged.append(job)
                continue
            if job_id in seen_ids:
                duplicate_count += 1
                continue
            seen_ids.add(job_id)
            merged.append(job)

    if duplicate_count:
        print(f"[merge] Deduplicated {duplicate_count} duplicate job(s) (kept first occurrence).")

    # -----------------------------------------------------------------------
    # Write output
    # -----------------------------------------------------------------------
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w") as fh:
        json.dump(merged, fh, indent=2)

    print(
        f"[merge] Done. {len(merged)} unique jobs written to {output_path}  "
        f"(bulk_low={len(bulk_low)}, bulk_3={len(bulk_3)}, chunks={chunk_jobs_total})."
    )


if __name__ == "__main__":
    main()
