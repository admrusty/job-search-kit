#!/usr/bin/env bash
#
# run_wide_pipeline.sh — deterministic orchestrator for the Job Scout WIDE ATS pipeline.
#
# Design: a plain shell script is the boss. It runs every non-LLM stage directly
# (scrape, chunk, merge, build, alerts, seen-IDs, git) and invokes `claude -p`
# ONLY for the three stages that genuinely need a model: scoring, alert review,
# and Slack posting. This removes the "agent babysits a 15-minute subprocess and
# yields for a resume" failure that breaks the monolithic headless run, and makes
# the pipeline robust for unattended launchd execution — fully off the desktop app.
#
# Each `claude -p` call is a short, self-contained turn (no resume needed).
# Mirrors the stages defined in SKILL-wide-scraper.md.

set -uo pipefail

SCOUT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
XLSX_PATH="$SCOUT_DIR/Job Scout Jobs.xlsx"
SEEN_PATH="$SCOUT_DIR/job-scout-seen.json"
SLACK_CHANNEL="$(python3 -c "import json;print(json.load(open('$SCOUT_DIR/config/jobscout_config.json')).get('slackChannel',''))" 2>/dev/null)"

# launchd hands jobs a minimal PATH; set a full one so python3/node/git/claude resolve.
export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin:${PATH:-}"

cd "$SCOUT_DIR" || { echo "SCOUT_DIR missing: $SCOUT_DIR" >&2; exit 1; }
mkdir -p logs
LOG="$SCOUT_DIR/logs/wide-$(date +%Y-%m-%d_%H%M%S).log"

CLAUDE_BIN="$(command -v claude || true)"
[ -z "$CLAUDE_BIN" ] && { echo "claude CLI not found on PATH" >&2; exit 1; }

# Everything below is logged to the dated file (and echoed to stdout for interactive runs).
exec > >(tee -a "$LOG") 2>&1

log()  { echo "[$(date '+%H:%M:%S')] $*"; }
fail() { echo "[$(date '+%H:%M:%S')] FAILED: $*"; python3 - "$1" <<'PY'
import json, sys, os
p = "run/run_summary.json"
try:
    s = json.load(open(p))
except Exception:
    s = {}
s.setdefault("failedSteps", []).append(sys.argv[1])
s["status"] = "partial"
json.dump(s, open(p, "w"), indent=2)
PY
}
set_step() { python3 - "$1" <<'PY'
import json, sys
p = "run/run_summary.json"
s = json.load(open(p))
s["lastCompletedStep"] = int(sys.argv[1])
json.dump(s, open(p, "w"), indent=2)
PY
}

# claude -p helper: one bounded, self-contained turn. No resume, no backgrounding.
run_agent_stage() {
  local label="$1" prompt="$2"
  log "claude -p stage: $label"
  "$CLAUDE_BIN" -p "$prompt" --dangerously-skip-permissions
  local rc=$?
  log "claude -p stage '$label' exited $rc"
  return $rc
}

log "=== WIDE pipeline started ($(date '+%Y-%m-%d %H:%M:%S %Z')) | claude: $CLAUDE_BIN ==="

# ---------------------------------------------------------------------------
# Step 0 — Setup: fresh run dir, lock (age-check), run_summary init
# ---------------------------------------------------------------------------
python3 - <<'PY'
import os, time, glob, shutil, json, sys
# Rename any existing run/ aside (FUSE-free local disk; rename always works).
if os.path.exists("run") or os.path.islink("run"):
    os.rename("run", f"run.stale_{int(time.time())}")
# Prune run.stale_* older than 7 days.
for d in glob.glob("run.stale_*"):
    try:
        if time.time() - os.path.getmtime(d) > 7*86400:
            shutil.rmtree(d)
    except Exception:
        pass
os.makedirs("run/chunks", exist_ok=True)
# Lock: abort only if a *live* (<45min) lock exists.
lock = "run.lock"
if os.path.exists(lock):
    age = time.time() - os.path.getmtime(lock)
    if age < 2700:
        print(f"Live lock (age {age:.0f}s) — aborting."); sys.exit(3)
    os.rename(lock, lock + ".stale")
open(lock, "w").close()
json.dump({
    "startedAt": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
    "finishedAt": None, "status": "started", "source": "wide",
    "scrapedCount": 0, "newCount": 0, "chunkCount": 0, "scoredCount": 0,
    "alertCount": 0, "failedSteps": [], "warnings": [],
    "slackPosted": {"score9": False, "score8": False, "score7": False},
    "lastCompletedStep": 0,
}, open("run/run_summary.json", "w"), indent=2)
print("Step 0 setup complete.")
PY
rc=$?
if [ "$rc" = "3" ]; then log "Aborting: live lock held by another run."; exit 0; fi
if [ "$rc" != "0" ]; then log "Step 0 setup failed (rc=$rc); aborting."; exit 1; fi
trap 'rm -f "$SCOUT_DIR/run.lock"' EXIT

# ---------------------------------------------------------------------------
# Step 1 — Wide ATS scrape (8-15 min, deterministic; plain subprocess)
# ---------------------------------------------------------------------------
log "Step 1: wide ATS scrape (this takes 8-15 min)…"
python3 jobscout_wide_scraper.py \
    --employers config/target_employers.json \
    --slugs     config/slug_cache \
    --seen      job-scout-seen.json \
    --config    config/jobscout_config.json \
    --run-dir   run \
    --max-age-days 7 \
    --workers   10
scrape_rc=$?
if [ "$scrape_rc" != "0" ]; then fail "step1_scrape"; fi

WIDE_COUNT=$(python3 -c "
from pathlib import Path
p = Path('run/raw_wide.jsonl')
print(len([l for l in p.read_text().splitlines() if l.strip()]) if p.exists() else 0)
" 2>/dev/null || echo 0)
log "Scrape produced $WIDE_COUNT new jobs."
python3 - "$WIDE_COUNT" <<'PY'
import json, sys
s = json.load(open("run/run_summary.json"))
s["scrapedCount"] = int(sys.argv[1]); s["newCount"] = int(sys.argv[1]); s["lastCompletedStep"] = 1
json.dump(s, open("run/run_summary.json", "w"), indent=2)
PY

if [ "${WIDE_COUNT:-0}" -eq 0 ]; then
  log "No new jobs — skipping scoring/build/alerts; jumping to seen-IDs + git."
  SKIP_TO_SEEN=1
else
  SKIP_TO_SEEN=0
fi

if [ "$SKIP_TO_SEEN" = "0" ]; then
  # -------------------------------------------------------------------------
  # Step 2 — Prep (copy raw_wide -> raw_desc; descriptions already present)
  # -------------------------------------------------------------------------
  log "Step 2: prep raw_desc.jsonl"
  python3 -c "
from pathlib import Path
src, dst = Path('run/raw_wide.jsonl'), Path('run/raw_desc.jsonl')
if src.exists() and src.stat().st_size > 0:
    dst.write_text(src.read_text()); print('Copied to raw_desc.jsonl')
" && set_step 2 || fail "step2_prep"

  # -------------------------------------------------------------------------
  # Step 3 — Chunk
  # -------------------------------------------------------------------------
  log "Step 3: chunk"
  python3 "$SCOUT_DIR/jobscout_prep.py" --stage chunk --run-dir run && set_step 3 || fail "step3_chunk"

  CHUNKS=$(python3 -c "import json;print(len(json.load(open('run/chunk_manifest.json')).get('chunks', [])))" 2>/dev/null || echo 0)
  log "Chunk manifest: $CHUNKS chunks"

  # -------------------------------------------------------------------------
  # Step 4 — LLM SCORING  (claude -p; spawns scoring sub-agents in-turn)
  # -------------------------------------------------------------------------
  if [ "${CHUNKS:-0}" -gt 0 ]; then
    log "Step 4: scoring $CHUNKS chunk(s) via per-chunk claude -p (sequential, no sub-agents)"
    python3 "$SCOUT_DIR/jobscout_llm_stage.py" score --run-dir run --concurrency 1
    MISSING=$(python3 -c "
import json, os
m = json.load(open('run/chunk_manifest.json'))
ch = m.get('chunks', m if isinstance(m, list) else [])
print(sum(1 for c in ch if not os.path.exists(c.get('output_path',''))))
" 2>/dev/null || echo 99)
    if [ "${MISSING:-99}" = "0" ]; then set_step 4; log "Scoring complete (all chunk outputs present)."; else fail "step4_scoring"; log "Scoring incomplete: $MISSING chunk output(s) missing."; fi
  else
    log "No chunks to score."
    set_step 4
  fi

  # -------------------------------------------------------------------------
  # Step 5 — Merge + build (deterministic)
  # -------------------------------------------------------------------------
  log "Step 5: merge + build"
  python3 "$SCOUT_DIR/jobscout_merge.py" --run-dir run --output run/scored.json \
    && python3 "$SCOUT_DIR/jobscout_build.py" --new run/scored.json --data data.json \
         --xlsx "$XLSX_PATH" --applied job-scout-applied.json \
    && set_step 5 || fail "step5_build"

  # -------------------------------------------------------------------------
  # Step 6 — Alert candidates (deterministic) + REVIEW (claude -p)
  # -------------------------------------------------------------------------
  log "Step 6a: generate alert candidates"
  if python3 "$SCOUT_DIR/jobscout_alerts.py" \
        --data "$SCOUT_DIR/data.json" --state "$SCOUT_DIR/job-scout-state.json" \
        --applied "$SCOUT_DIR/job-scout-applied.json" \
        --config "$SCOUT_DIR/config/jobscout_config.json" \
        --out "$SCOUT_DIR/run/alert_candidates.json"; then

    PENDING=$(python3 -c "
import json
c = json.load(open('run/alert_candidates.json')).get('candidates', [])
print(sum(1 for x in c if x.get('alertApproved') is None))
" 2>/dev/null || echo 0)
    log "Alert candidates needing review: $PENDING"

    if [ "${PENDING:-0}" -gt 0 ]; then
      log "Step 6b: reviewing $PENDING candidate(s) via per-candidate claude -p (sequential, no sub-agents)"
      python3 "$SCOUT_DIR/jobscout_llm_stage.py" review --run-dir run --concurrency 1
    fi
    # Recompute review counts deterministically.
    python3 -c "
import json
p = json.load(open('run/alert_candidates.json')); c = p.get('candidates', [])
ap = sum(1 for x in c if x.get('alertApproved') is True)
rj = sum(1 for x in c if x.get('alertApproved') is False)
s = json.load(open('run/run_summary.json'))
s['alertReviewedCount']=len(c); s['alertRejectedCount']=rj; s['alertCount']=ap
json.dump(s, open('run/run_summary.json','w'), indent=2)
print(f'Alert review: {ap} approved, {rj} rejected')
" && set_step 6 || fail "step6_reviewcount"

    # -----------------------------------------------------------------------
    # Step 7 — Slack alerts (claude -p; Slack MCP) + mark alerted
    # -----------------------------------------------------------------------
    APPROVED=$(python3 -c "import json;print(sum(1 for x in json.load(open('run/alert_candidates.json')).get('candidates',[]) if x.get('alertApproved') is True))" 2>/dev/null || echo 0)
    if [ -z "$SLACK_CHANNEL" ]; then
      log "Slack not configured — skipping (alerts are in the dashboard)"
    elif [ "${APPROVED:-0}" -gt 0 ]; then
      SLACK_PROMPT="You are a non-interactive stage runner for the Job Scout wide pipeline. Working dir: ${SCOUT_DIR}. Do ONLY Slack posting. Read run/alert_candidates.json. Post to Slack channel ${SLACK_CHANNEL} (via the Slack MCP tool) one message per job where ALL are true: alertApproved==true, _score>=7, geoFit==true, applied==false, caExcluded==false, and (remote jobs: salary>=143000 OR salary unknown/contract; OC jobs: no salary threshold). Format each message exactly:\n[Score N] Job Title — Company\nLocation | Salary\n<1-line reason>\n<Job URL>\nPost highest scores first. To stay idempotent across tiers, read run/run_summary.json 'slackPosted' before each score tier (score9/score8/score7); skip a tier already true; after posting a tier set it true and write run_summary.json. Do everything in THIS turn — no background, no wakeups, do not yield. Finish by printing: SLACK_DONE <count> posted."
      run_agent_stage "slack" "$SLACK_PROMPT" || fail "step7_slack"
    else
      log "No approved alerts to post."
    fi
    log "Step 7b: mark alerted in state"
    python3 "$SCOUT_DIR/jobscout_mark_alerted.py" \
      --candidates "$SCOUT_DIR/run/alert_candidates.json" \
      --state "$SCOUT_DIR/job-scout-state.json" || fail "step7_markalerted"
    set_step 7
  else
    log "Alert candidate generation failed — skipping review + Slack."
    fail "step6_alertgen"
  fi
fi

# Capture counts now — Step 8 (seen) rewrites run_summary.json in its own format.
NEW=$(python3 -c "import json;s=json.load(open('run/run_summary.json'));print(s.get('newCount', s.get('scrapedCount','?')))" 2>/dev/null || echo '?')
AL=$(python3 -c "import json;s=json.load(open('run/run_summary.json'));print(s.get('alertCount','?'))" 2>/dev/null || echo '?')

# ---------------------------------------------------------------------------
# Step 8 — Save seen IDs (deterministic; always runs)
# ---------------------------------------------------------------------------
log "Step 8: save seen IDs"
python3 "$SCOUT_DIR/jobscout_seen.py" \
  --seen "$SEEN_PATH" --data "$SCOUT_DIR/data.json" \
  --config "$SCOUT_DIR/config/jobscout_config.json" \
  --run-dir run --run-summary-out run/run_summary.json \
  && set_step 8 || fail "step8_seen"

# ---------------------------------------------------------------------------
# Step 9 — Archive run summary
# ---------------------------------------------------------------------------
mkdir -p run_summaries
cp run/run_summary.json "run_summaries/run_summary_wide_$(date +%Y-%m-%d_%H%M%S).json" 2>/dev/null || true

# Finalize status.
python3 - <<'PY'
import json, time
s = json.load(open("run/run_summary.json"))
s["finishedAt"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
if s.get("status") != "partial":
    s["status"] = "success"
json.dump(s, open("run/run_summary.json", "w"), indent=2)
print("Final status:", s["status"], "| new:", s.get("newCount"), "| alerts:", s.get("alertCount"), "| failed:", s.get("failedSteps"))
PY

# ---------------------------------------------------------------------------
# Step 10 — Git commit + push (deterministic)
# ---------------------------------------------------------------------------
log "Step 10: git commit + push (${NEW} new, ${AL} alerts)"
# Clear any stale git index lock (local disk, safe).
[ -f .git/index.lock ] && mv .git/index.lock .git/index.lock.stale 2>/dev/null || true
git add \
  data.json job-scout-seen.json job-scout-state.json job-scout-applied.json \
  run/run_summary.json run_summaries/ 2>/dev/null || true
git -c user.name="Job Scout" -c user.email="scout@local" \
  commit -m "job-scout-wide run $(date +%Y-%m-%d): ${NEW} new jobs, ${AL} alerts" \
  || echo "Nothing to commit."
if git remote get-url origin >/dev/null 2>&1; then
  git push origin HEAD || echo "git push failed (committed locally; next run catches up)."
else
  log "no git remote — skipping push (local commits only)"
fi

log "=== WIDE pipeline finished ($(date '+%Y-%m-%d %H:%M:%S %Z')) ==="
echo "log: $LOG"
