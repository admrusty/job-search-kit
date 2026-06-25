"""Job Scout — preparation script.

Two-stage pipeline helper:
  Stage "filter":  reads metadata scans → title-scores new jobs → splits into
                   bulk_low (score 1–2) and needs_desc (score 3+).
  Stage "chunk":   reads raw descriptions → title-rescores → splits score-3
                   into bulk_3 and score-4+ into LLM chunks.

Usage:
  python3 jobscout_prep.py --stage filter --seen <path> --run-dir run
  python3 jobscout_prep.py --stage chunk --run-dir run
"""

import argparse
import json
import random
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Title-scoring patterns
# ---------------------------------------------------------------------------

_SCORE1_PATTERNS = [
    "software engineer", "software developer", "full stack", "frontend developer",
    "backend developer", "devops", "cloud engineer", "platform engineer",
    "data engineer", "machine learning", "ml engineer", "data scientist",
    "ios developer", "android developer", "account executive",
    "sales representative", "account manager", "business development",
    "sdr", "bdr", "revenue operations",
    "tax ", "payroll", "accounts payable", "accounts receivable",
    "financial analyst", "finance manager", "controller", "actuary",
    "warehouse", "logistics", "supply chain", "forklift",
    "construction", "hvac", "electrician", "plumber",
    "retail", "cashier", "barista", "chef", "restaurant",
    "clinical ", "nurse", "physician", "medical director", "dialysis",
    "pharmacist", "recruiter", "talent acquisition", "hr generalist",
    "hr business partner", "dental", "optometrist", "radiolog",
    # L&D / instructional design — out of scope
    "instructional designer", "instructional design",
    "learning experience designer", "learning designer",
    "curriculum designer", "curriculum developer", "curriculum manager",
    "training specialist", "training manager", "corporate trainer",
    "lms administrator", "lms manager", "learning and development",
    "talent development", "e-learning", "elearning",
    "ld specialist", "l&d specialist", "l&d manager",
    "enablement trainer", "sales trainer", "training and development",
    "organizational development", "od consultant", "course designer",
    "course developer", "learning consultant",
]

_SCORE2_PATTERNS = [
    "product manager", "product owner", "product operations",
    "media buyer", "paid media", "performance marketing",
    "business analyst", "systems analyst",
    "project manager", "scrum master", "delivery manager",
    "customer success", "client success",
    "solutions architect", "solutions engineer",
    "data product", "it support", "help desk", "network engineer",
]

_SCORE3_PATTERNS = [
    "technical writer", "technical writing", "documentation", "localization",
    "social media", "communications manager", "marketing manager",
    "brand manager", "change management", "organizational change",
    "graphic design", "ux designer", "ui designer",
    "copywriter", "copy editor",
]

# If ANY of these appear alongside a score-3 pattern match, escalate to LLM
# (e.g. "Technical Writer, Manager" is a leadership role, not a junior writer)
_MANAGEMENT_ESCALATORS = [
    "manager", "director", "lead", "head of", "senior", "principal", "vp ", "vice president",
]


def title_score(title: str) -> tuple[int, str]:
    """Return (score, reason) for a job title.

    Score meanings:
      1 — clearly irrelevant domain, skip without description
      2 — low-relevance role type, skip without description
      3 — peripherally relevant, save description for reference
      4 — send to LLM (placeholder; reason left empty)
    """
    t = title.lower()

    for pattern in _SCORE1_PATTERNS:
        if pattern in t:
            return (
                1,
                f"Title matches clearly-irrelevant domain '{pattern.strip()}'; "
                "auto-scored 1 without description.",
            )

    for pattern in _SCORE2_PATTERNS:
        if pattern in t:
            return (
                2,
                f"Title '{title}' is a low-relevance role type; "
                "auto-scored 2 without description.",
            )

    for pattern in _SCORE3_PATTERNS:
        if pattern in t:
            # Escalate to LLM if a management/seniority keyword is also present
            # e.g. "Technical Writer, Manager" is a leadership role worth scoring
            if any(esc in t for esc in _MANAGEMENT_ESCALATORS):
                return (4, "")
            return (
                3,
                f"Title '{title}' is peripherally relevant; "
                "auto-scored 3, description saved for reference.",
            )

    # Everything else goes to the LLM.
    return (4, "")


# ---------------------------------------------------------------------------
# JSONL helpers
# ---------------------------------------------------------------------------

def read_jsonl(path: Path) -> list[dict]:
    """Read a JSONL file; return list of parsed objects. Empty if file missing."""
    if not path.exists():
        print(f"[prep] WARNING: {path} not found — treating as empty.", file=sys.stderr)
        return []
    items = []
    with path.open() as fh:
        for lineno, line in enumerate(fh, 1):
            line = line.strip()
            if not line:
                continue
            try:
                items.append(json.loads(line))
            except json.JSONDecodeError as exc:
                print(
                    f"[prep] WARNING: {path}:{lineno} — JSON parse error: {exc}",
                    file=sys.stderr,
                )
    return items


def write_json(path: Path, data) -> None:
    """Write data as pretty-printed JSON, creating parent dirs as needed."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w") as fh:
        json.dump(data, fh, indent=2)
    print(f"[prep] Wrote {path} ({len(data) if isinstance(data, (list, dict)) else '?'} items)")


# ---------------------------------------------------------------------------
# Filter-stage constants
# ---------------------------------------------------------------------------

TITLE_KEYWORDS_FOR_LOW_SCORE_FETCH = [
    "manager", "senior", "lead", "program", "product",
    "content", "knowledge", "digital", "ai", "adoption",
    "enablement", "operations", "experience", "director",
    "specialist", "strategist", "architect",
]


def _load_target_companies(run_dir: Path) -> set[str]:
    """Load target company list from config/jobscout_config.json.

    Returns a set of lowercased company names, or an empty set if the config
    file is missing or does not contain a target company list.
    """
    # Look for config relative to the script's own directory, not run_dir.
    script_dir = Path(__file__).parent
    config_path = script_dir / "config" / "jobscout_config.json"
    if not config_path.exists():
        return set()
    try:
        with config_path.open() as fh:
            cfg = json.load(fh)
        companies = cfg.get("targetCompanies") or cfg.get("target_companies") or []
        if not isinstance(companies, list):
            return set()
        return {str(c).lower() for c in companies if c}
    except Exception:
        return set()


# ---------------------------------------------------------------------------
# Stage: filter
# ---------------------------------------------------------------------------

def load_seen_ids(seen_path: Path) -> set[str]:
    """Load seen IDs from a JSON file.

    Accepts both {"ids": [...]} and a plain list [...].
    Returns a set of string IDs.
    """
    if not seen_path.exists():
        print(f"[prep] WARNING: seen-IDs file {seen_path} not found — treating as empty.", file=sys.stderr)
        return set()
    with seen_path.open() as fh:
        raw = json.load(fh)
    if isinstance(raw, list):
        ids = raw
    elif isinstance(raw, dict):
        ids = raw.get("ids", [])
    else:
        print("[prep] WARNING: seen-IDs file has unexpected format — treating as empty.", file=sys.stderr)
        return set()
    return {str(i) for i in ids}


def stage_filter(run_dir: Path, seen_path: Path) -> None:
    """Stage 'filter': identify new jobs, title-score, split into bulk vs needs_desc."""
    scan_remote = read_jsonl(run_dir / "scan_remote.jsonl")
    scan_oc = read_jsonl(run_dir / "scan_oc.jsonl")

    all_items = scan_remote + scan_oc
    if not all_items:
        print("[prep] No items found in scan files — nothing to do.", file=sys.stderr)
        # Write empty outputs so downstream steps don't fail.
        write_json(run_dir / "bulk_low.json", [])
        write_json(run_dir / "needs_desc.json", {"remote": [], "oc": []})
        write_json(run_dir / "filter_summary.json", {
            "total_scanned": 0, "new_count": 0,
            "bulk_low_count": 0, "to_score_count": 0,
            "selectedForDescriptionFetch": 0,
            "selectedByRuleB": 0,
            "scoreDistribution": {
                "0": 0, "1": 0, "2_no_keywords": 0,
                "2_with_keywords": 0, "3": 0, "4": 0, "5plus": 0,
            },
        })
        return

    seen_ids = load_seen_ids(seen_path)
    target_companies = _load_target_companies(run_dir)
    print(f"[prep] Loaded {len(seen_ids)} seen IDs.")
    if target_companies:
        print(f"[prep] Loaded {len(target_companies)} target companies for Rule C.")
    print(f"[prep] Scanned {len(all_items)} total items ({len(scan_remote)} remote, {len(scan_oc)} OC).")

    bulk_low: list[dict] = []
    needs_remote: list[dict] = []
    needs_oc: list[dict] = []
    rejected_pool: list[dict] = []  # for audit sample

    # Score-bracket counters
    dist: dict[str, int] = {
        "0": 0, "1": 0, "2_no_keywords": 0,
        "2_with_keywords": 0, "3": 0, "4": 0, "5plus": 0,
    }
    rule_b_count = 0  # selected only because of Rule B (not Rule A)

    new_count = 0
    for item in all_items:
        job_id = str(item.get("id", ""))
        if not job_id:
            print(f"[prep] WARNING: item missing 'id' field — skipping: {item}", file=sys.stderr)
            continue
        if job_id in seen_ids:
            continue

        new_count += 1
        title = item.get("title", "")
        company = item.get("companyName", "")
        score, reason = title_score(title)

        # --- Score distribution tracking ---
        if score == 0:
            dist["0"] += 1
        elif score == 1:
            dist["1"] += 1
        elif score == 2:
            # Determine Rule B eligibility now (needed for both dist and selection)
            title_lower = title.lower()
            matched_keyword = next(
                (kw for kw in TITLE_KEYWORDS_FOR_LOW_SCORE_FETCH if kw in title_lower),
                None,
            )
            if matched_keyword:
                dist["2_with_keywords"] += 1
            else:
                dist["2_no_keywords"] += 1
        elif score == 3:
            dist["3"] += 1
        elif score == 4:
            dist["4"] += 1
        else:
            dist["5plus"] += 1

        # --- Selection rules ---
        selected = False
        selected_reason = ""

        # Rule A: score >= 3 (original logic)
        if score >= 3:
            selected = True
            selected_reason = "score >= 3"

        # Rule B: score == 2 with a matching title keyword
        if not selected and score == 2:
            title_lower = title.lower()
            matched_keyword = next(
                (kw for kw in TITLE_KEYWORDS_FOR_LOW_SCORE_FETCH if kw in title_lower),
                None,
            )
            if matched_keyword:
                selected = True
                selected_reason = f"score 2 with title keyword: {matched_keyword}"
                rule_b_count += 1

        # Rule C: score <= 2 with a target company match
        if not selected and score <= 2 and target_companies:
            if company.lower() in target_companies:
                selected = True
                selected_reason = f"target company: {company}"

        if selected:
            # Needs description fetch.
            # Preserve _offset so Claude can fetch by position in dataset.
            entry = {
                "id": job_id,
                "offset": item.get("_offset"),
                "title": title,
                "companyName": company,
                "location": item.get("location", ""),
                "postedAt": item.get("postedAt", ""),
                "_src": item.get("_src", ""),
                "selectedReason": selected_reason,
            }
            src = item.get("_src", "")
            if src == "oc":
                needs_oc.append(entry)
            else:
                needs_remote.append(entry)
        else:
            item = dict(item)  # shallow copy before mutating
            item["_score"] = score
            item["_reason"] = reason
            bulk_low.append(item)
            # Add to rejected pool for audit sampling
            rejected_pool.append({
                "id": job_id,
                "title": title,
                "companyName": company,
                "location": item.get("location", ""),
                "_score": score,
                "selectedReason": "not selected",
            })

    to_score_count = len(needs_remote) + len(needs_oc)
    print(
        f"[prep] New jobs: {new_count} total — "
        f"{len(bulk_low)} bulk-low, "
        f"{to_score_count} need description fetch "
        f"(Rule A/B/C; {rule_b_count} added by Rule B alone)."
    )

    write_json(run_dir / "bulk_low.json", bulk_low)
    write_json(run_dir / "needs_desc.json", {"remote": needs_remote, "oc": needs_oc})
    write_json(run_dir / "filter_summary.json", {
        "total_scanned": len(all_items),
        "new_count": new_count,
        "bulk_low_count": len(bulk_low),
        "to_score_count": to_score_count,
        "needs_remote_count": len(needs_remote),
        "needs_oc_count": len(needs_oc),
        "selectedForDescriptionFetch": to_score_count,
        "selectedByRuleB": rule_b_count,
        "scoreDistribution": dist,
    })

    # Write audit sample: up to 50 randomly-sampled rejected jobs.
    audit_path = run_dir / "filter_audit_sample.jsonl"
    audit_path.parent.mkdir(parents=True, exist_ok=True)
    sample = random.sample(rejected_pool, min(50, len(rejected_pool)))
    with audit_path.open("w") as fh:
        for entry in sample:
            fh.write(json.dumps(entry) + "\n")
    print(f"[prep] Wrote {audit_path} ({len(sample)} audit samples from {len(rejected_pool)} rejected jobs).")


# ---------------------------------------------------------------------------
# Stage: chunk
# ---------------------------------------------------------------------------

_CHUNK_SIZE = 20


def stage_chunk(run_dir: Path) -> None:
    """Stage 'chunk': read raw descriptions, split into bulk_3 and LLM chunks."""
    raw_items = read_jsonl(run_dir / "raw_desc.jsonl")
    if not raw_items:
        print("[prep] No items in raw_desc.jsonl — nothing to chunk.", file=sys.stderr)
        write_json(run_dir / "bulk_3.json", [])
        write_json(run_dir / "chunk_manifest.json", {"chunks": []})
        return

    print(f"[prep] Chunking {len(raw_items)} description-fetched jobs.")

    chunks_dir = run_dir / "chunks"
    chunks_dir.mkdir(parents=True, exist_ok=True)

    bulk_3: list[dict] = []
    llm_jobs: list[dict] = []

    for item in raw_items:
        title = item.get("title", "")
        score, reason = title_score(title)
        item = dict(item)  # shallow copy before mutating

        if score <= 3:
            # Score 1 and 2 here are unusual (they should have been filtered out
            # already), but handle gracefully just in case.
            item["_score"] = score
            item["_reason"] = reason
            bulk_3.append(item)  # name kept as bulk_3 for simplicity; merge step handles it
        else:
            # Score 4 → LLM
            llm_jobs.append(item)

    # Write score-3 bulk file.
    write_json(run_dir / "bulk_3.json", bulk_3)
    print(f"[prep] bulk_3: {len(bulk_3)} jobs (score ≤ 3 with description).")
    print(f"[prep] LLM jobs: {len(llm_jobs)} jobs to chunk.")

    # Split LLM jobs into chunks of _CHUNK_SIZE.
    manifest_chunks = []
    chunk_num = 0
    for start in range(0, max(len(llm_jobs), 1), _CHUNK_SIZE):
        batch = llm_jobs[start : start + _CHUNK_SIZE]
        if not batch:
            break
        chunk_num += 1
        chunk_name = f"chunk_{chunk_num:02d}.jsonl"
        output_name = f"scored_chunk_{chunk_num:02d}.json"
        chunk_path = chunks_dir / chunk_name
        output_path = chunks_dir / output_name

        # Write chunk as JSONL.
        with chunk_path.open("w") as fh:
            for job in batch:
                fh.write(json.dumps(job) + "\n")
        print(f"[prep] Wrote {chunk_path} ({len(batch)} jobs).")

        manifest_chunks.append({
            "chunk_path": str(chunk_path),
            "output_path": str(output_path),
            "count": len(batch),
        })

    write_json(run_dir / "chunk_manifest.json", {"chunks": manifest_chunks})
    print(f"[prep] chunk_manifest: {len(manifest_chunks)} chunk(s).")


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Job Scout preparation: filter metadata scans or chunk descriptions for LLM scoring."
    )
    parser.add_argument(
        "--stage",
        required=True,
        choices=["filter", "chunk"],
        help="Which stage to run: 'filter' (metadata → bulk_low + needs_desc) or 'chunk' (descriptions → bulk_3 + chunks).",
    )
    parser.add_argument(
        "--run-dir",
        default="run",
        help="Directory for intermediate run files (default: run).",
    )
    parser.add_argument(
        "--seen",
        help="Path to seen-IDs JSON file (required for --stage filter).",
    )
    args = parser.parse_args()

    run_dir = Path(args.run_dir)

    if args.stage == "filter":
        if not args.seen:
            print("[prep] ERROR: --seen is required for --stage filter.", file=sys.stderr)
            sys.exit(1)
        seen_path = Path(args.seen)
        stage_filter(run_dir, seen_path)

    elif args.stage == "chunk":
        stage_chunk(run_dir)


if __name__ == "__main__":
    main()
