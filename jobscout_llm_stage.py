"""jobscout_llm_stage.py — run the LLM stages (scoring, alert review) as a pool of
independent, single-turn `claude -p` calls.

Why: spawning parallel sub-agents from inside a single headless `claude -p` wedges
(the agent blocks waiting on sub-agent machinery that never resolves). Instead, this
driver calls `claude -p` ONCE PER unit of work (one chunk to score, one candidate to
review) as a plain subprocess, parallelized with a thread pool. Each call runs a
self-contained LEAF prompt (jobscout_score_agent.md / jobscout_alert_reviewer.md) that
does the work itself — no nested sub-agents — so nothing hangs.

Usage:
  python3 jobscout_llm_stage.py score  --run-dir run [--concurrency 4]
  python3 jobscout_llm_stage.py review --run-dir run [--concurrency 4]
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

SCOUT_DIR = Path(__file__).resolve().parent
CLAUDE = os.environ.get("CLAUDE_BIN") or shutil.which("claude") or "/usr/local/bin/claude"

NO_SUBAGENT = (
    "SINGLE-TURN NON-INTERACTIVE TASK. Do the work yourself in THIS turn. "
    "Do NOT spawn sub-agents or use the Agent/Task tool. Do NOT run anything in the "
    "background, do NOT schedule a wakeup, and do NOT end your turn until the task is "
    "complete.\n\n"
)


def run_claude(prompt: str, timeout: int) -> tuple[int, str, str]:
    try:
        r = subprocess.run(
            [CLAUDE, "-p", prompt, "--dangerously-skip-permissions"],
            capture_output=True, text=True, timeout=timeout, cwd=str(SCOUT_DIR),
        )
        return r.returncode, r.stdout, r.stderr
    except subprocess.TimeoutExpired:
        return 124, "", f"timeout after {timeout}s"


def extract_json(text: str):
    """Pull the first JSON object/array out of an LLM response (handles ``` fences)."""
    if not text:
        return None
    m = re.search(r"```(?:json)?\s*(.+?)```", text, re.DOTALL)
    blob = m.group(1).strip() if m else text.strip()
    for opener, closer in (("{", "}"), ("[", "]")):
        i, j = blob.find(opener), blob.rfind(closer)
        if i != -1 and j != -1 and j > i:
            try:
                return json.loads(blob[i:j + 1])
            except Exception:
                pass
    try:
        return json.loads(blob)
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------

def score(run_dir: Path, concurrency: int) -> int:
    manifest = json.loads((run_dir / "chunk_manifest.json").read_text())
    chunks = manifest.get("chunks", []) if isinstance(manifest, dict) else manifest
    if not chunks:
        print("[score] no chunks"); return 0
    tmpl = (SCOUT_DIR / "jobscout_score_agent.md").read_text()

    def do(c):
        prompt = NO_SUBAGENT + tmpl.replace("{CHUNK_PATH}", c["chunk_path"]).replace("{OUTPUT_PATH}", c["output_path"])
        rc, out, err = run_claude(prompt, timeout=900)
        ok = os.path.exists(c["output_path"])
        print(f"[score] {Path(c['chunk_path']).name} rc={rc} output={'ok' if ok else 'MISSING'}", flush=True)
        return ok

    with ThreadPoolExecutor(max_workers=concurrency) as ex:
        list(ex.map(do, chunks))
    # One sequential retry for any missing output.
    for c in chunks:
        if not os.path.exists(c["output_path"]):
            print(f"[score] retry {Path(c['chunk_path']).name}", flush=True)
            do(c)

    missing = [c["output_path"] for c in chunks if not os.path.exists(c["output_path"])]
    print(f"[score] {len(chunks) - len(missing)}/{len(chunks)} chunks scored"
          + (f"; MISSING={missing}" if missing else ""))
    return 0 if not missing else 1


# ---------------------------------------------------------------------------
# Alert review
# ---------------------------------------------------------------------------

def review(run_dir: Path, concurrency: int) -> int:
    path = run_dir / "alert_candidates.json"
    payload = json.loads(path.read_text())
    cands = payload.get("candidates", [])
    pending = [c for c in cands if c.get("alertApproved") is None]
    if not pending:
        print("[review] no pending candidates"); return 0
    tmpl = (SCOUT_DIR / "jobscout_alert_reviewer.md").read_text()

    def do(c):
        prompt = NO_SUBAGENT + tmpl.replace("{CANDIDATE_JSON}", json.dumps(c, ensure_ascii=False))
        rc, out, err = run_claude(prompt, timeout=300)
        dec = extract_json(out)
        return str(c.get("id")), dec

    decisions: dict = {}
    with ThreadPoolExecutor(max_workers=concurrency) as ex:
        for cid, dec in ex.map(do, pending):
            if dec:
                decisions[cid] = dec

    for c in cands:
        d = decisions.get(str(c.get("id")))
        if not d:
            continue
        c["alertApproved"] = d.get("alertApproved")
        flags = set(c.get("riskFlags") or []) | set(d.get("riskFlags") or [])
        c["riskFlags"] = sorted(flags)
        if d.get("alertApproved") is False:
            c["alertRejectionReason"] = d.get("finalReason")

    path.write_text(json.dumps(payload, indent=2))
    ap = sum(1 for c in cands if c.get("alertApproved") is True)
    rj = sum(1 for c in cands if c.get("alertApproved") is False)
    reviewed = len([1 for cid in decisions])
    print(f"[review] reviewed {reviewed}/{len(pending)} pending — {ap} approved, {rj} rejected total")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("stage", choices=["score", "review"])
    ap.add_argument("--run-dir", default="run")
    ap.add_argument("--concurrency", type=int, default=1)  # parallel claude -p deadlocks headless
    a = ap.parse_args()
    run_dir = Path(a.run_dir)
    return score(run_dir, a.concurrency) if a.stage == "score" else review(run_dir, a.concurrency)


if __name__ == "__main__":
    sys.exit(main())
