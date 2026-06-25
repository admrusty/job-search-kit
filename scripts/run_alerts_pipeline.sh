#!/usr/bin/env bash
#
# run_alerts_pipeline.sh — deterministic orchestrator for the Job Scout ALERTS routine.
#
# Same robust pattern as run_wide_pipeline.sh: a plain shell script runs every
# non-LLM stage directly and invokes `claude -p` only for the LLM stages (scoring,
# alert review, Slack, Gmail sync) — one self-contained call per unit of work, run
# SEQUENTIALLY (parallel/sub-agent claude -p deadlocks headless).
#
# Scrape sources (both 36h lookback):
#   - LinkedIn via Apify REST  (jobscout_apify_linkedin.py — descriptions included)
#   - Targeted ATS boards      (jobscout_ats_scraper.py — Greenhouse/Lever/Ashby)
# The old skill's trigger->poll->metadata-scan->description-fetch steps are all
# collapsed into the REST scraper, so this follows the simplified wide-style flow.

set -uo pipefail

SCOUT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
XLSX_PATH="$SCOUT_DIR/Job Scout Jobs.xlsx"
SEEN_PATH="$SCOUT_DIR/job-scout-seen.json"
SLACK_CHANNEL="$(python3 -c "import json;print(json.load(open('$SCOUT_DIR/config/jobscout_config.json')).get('slackChannel',''))" 2>/dev/null)"
GMAIL_SYNC="$(python3 -c "import json;print(json.load(open('$SCOUT_DIR/config/jobscout_config.json')).get('gmailSync',False))" 2>/dev/null)"
LOOKBACK_HOURS=36

export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin:${PATH:-}"
cd "$SCOUT_DIR" || { echo "SCOUT_DIR missing: $SCOUT_DIR" >&2; exit 1; }
mkdir -p logs
LOG="$SCOUT_DIR/logs/alerts-$(date +%Y-%m-%d_%H%M%S).log"
CLAUDE_BIN="$(command -v claude || true)"
[ -z "$CLAUDE_BIN" ] && { echo "claude CLI not found on PATH" >&2; exit 1; }
exec > >(tee -a "$LOG") 2>&1

log()  { echo "[$(date '+%H:%M:%S')] $*"; }
fail() { echo "[$(date '+%H:%M:%S')] FAILED: $*"; python3 - "$1" <<'PY'
import json, sys
p="run/run_summary.json"
try: s=json.load(open(p))
except Exception: s={}
s.setdefault("failedSteps", []).append(sys.argv[1]); s["status"]="partial"
json.dump(s, open(p,"w"), indent=2)
PY
}
set_step() { python3 - "$1" <<'PY'
import json, sys
p="run/run_summary.json"; s=json.load(open(p)); s["lastCompletedStep"]=int(sys.argv[1])
json.dump(s, open(p,"w"), indent=2)
PY
}
run_agent_stage() {
  local label="$1" prompt="$2"
  log "claude -p stage: $label"
  "$CLAUDE_BIN" -p "$prompt" --dangerously-skip-permissions
  local rc=$?; log "claude -p stage '$label' exited $rc"; return $rc
}

log "=== ALERTS pipeline started ($(date '+%Y-%m-%d %H:%M:%S %Z')) | claude: $CLAUDE_BIN ==="

# ---------------------------------------------------------------------------
# Step 0 — Setup
# ---------------------------------------------------------------------------
python3 - <<'PY'
import os, time, glob, shutil, json, sys
if os.path.exists("run") or os.path.islink("run"):
    os.rename("run", f"run.stale_{int(time.time())}")
for d in glob.glob("run.stale_*"):
    try:
        if time.time()-os.path.getmtime(d) > 7*86400: shutil.rmtree(d)
    except Exception: pass
os.makedirs("run/chunks", exist_ok=True)
lock="run.lock"
if os.path.exists(lock):
    age=time.time()-os.path.getmtime(lock)
    if age < 2700: print(f"Live lock (age {age:.0f}s) — aborting."); sys.exit(3)
    os.rename(lock, lock+".stale")
open(lock,"w").close()
json.dump({"startedAt": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "finishedAt": None,
    "status":"started","source":"alerts","scrapedCount":0,"newCount":0,"chunkCount":0,
    "scoredCount":0,"alertCount":0,"appliedMatchCount":0,"failedSteps":[],"warnings":[],
    "slackPosted":{"score9":False,"score8":False,"score7":False},"lastCompletedStep":0},
    open("run/run_summary.json","w"), indent=2)
print("Step 0 setup complete.")
PY
rc=$?
if [ "$rc" = "3" ]; then log "Aborting: live lock held by another run."; exit 0; fi
if [ "$rc" != "0" ]; then log "Step 0 setup failed; aborting."; exit 1; fi
trap 'rm -f "$SCOUT_DIR/run.lock"' EXIT

# ---------------------------------------------------------------------------
# Step 1a — LinkedIn scrape (Apify REST, 36h, descriptions included)
# ---------------------------------------------------------------------------
if [ -f "$HOME/.config/job-scout/apify_token" ] || [ -n "${APIFY_TOKEN:-}" ]; then
  log "Step 1a: LinkedIn scrape (Apify REST, ${LOOKBACK_HOURS}h)…"
  python3 jobscout_apify_linkedin.py \
    --config config/jobscout_config.json --seen job-scout-seen.json \
    --run-dir run --max-age-hours "$LOOKBACK_HOURS"
  [ "$?" = "0" ] || fail "step1a_linkedin"
else
  log "Apify token not set — skipping LinkedIn (ATS-only)"
fi

# Cap-saturation heads-up: if a LinkedIn search returned its full cap, more jobs
# exist than we scraped — post a Slack notice so the query can be split.
if [ -n "$SLACK_CHANNEL" ] && [ -s run/linkedin_saturation.json ]; then
  SAT=$(python3 -c "import json;print('; '.join(f\"{x['label']} returned {x['raw']} (cap {x['cap']})\" for x in json.load(open('run/linkedin_saturation.json'))))" 2>/dev/null)
  log "LinkedIn cap saturation: $SAT — posting Slack heads-up"
  SAT_PROMPT="You are a non-interactive stage runner. Do ONLY this: post ONE message to Slack channel ${SLACK_CHANNEL} via the Slack MCP tool, then stop. Message text exactly: \":warning: Job Scout — LinkedIn scrape hit its result cap (${SAT}). More matching jobs likely exist than were scraped today. Consider splitting that search (narrow keywords or location) in config/jobscout_config.json.\" Post nothing else. No sub-agents, no background, no wakeups. Finish by printing: SAT_NOTICE_DONE."
  run_agent_stage "saturation-notice" "$SAT_PROMPT" || fail "step1a_saturation_notice"
fi

# ---------------------------------------------------------------------------
# Step 1b — Targeted ATS scrape (Greenhouse/Lever/Ashby, 36h)
# ---------------------------------------------------------------------------
log "Step 1b: targeted ATS scrape (${LOOKBACK_HOURS}h)…"
python3 jobscout_ats_scraper.py \
  --employers config/target_employers.json --seen job-scout-seen.json \
  --run-dir run --max-age-hours "$LOOKBACK_HOURS"
[ "$?" = "0" ] || fail "step1b_ats"

# ---------------------------------------------------------------------------
# Step 1c — ATS slug health check (Sundays only; warn-only)
# ---------------------------------------------------------------------------
if [ "$(date +%u)" = "7" ]; then
  log "Step 1c: Sunday ATS slug health check"
  python3 jobscout_slug_check.py --employers config/target_employers.json \
    --cache-dir config/slug_cache --out run/slug_check_report.json 2>/dev/null || true
  FAILS=$(python3 -c "import json;print(json.load(open('run/slug_check_report.json')).get('failedCount',0))" 2>/dev/null || echo 0)
  [ "${FAILS:-0}" -gt 0 ] && log "WARNING: $FAILS broken ATS slug(s) — see run/slug_check_report.json"
fi

# ---------------------------------------------------------------------------
# Step 2 — Assemble raw_desc.jsonl (LinkedIn + ATS), count
# ---------------------------------------------------------------------------
log "Step 2: assemble raw_desc.jsonl"
: > run/raw_desc.jsonl
[ -s run/raw_linkedin.jsonl ] && cat run/raw_linkedin.jsonl >> run/raw_desc.jsonl
[ -s run/raw_ats.jsonl ]      && cat run/raw_ats.jsonl      >> run/raw_desc.jsonl
RAW_COUNT=$(grep -c . run/raw_desc.jsonl 2>/dev/null || echo 0)
log "raw_desc.jsonl: $RAW_COUNT jobs (LinkedIn + ATS)"
python3 - "$RAW_COUNT" <<'PY'
import json, sys
s=json.load(open("run/run_summary.json"))
s["scrapedCount"]=int(sys.argv[1]); s["newCount"]=int(sys.argv[1]); s["lastCompletedStep"]=2
json.dump(s, open("run/run_summary.json","w"), indent=2)
PY

if [ "${RAW_COUNT:-0}" -eq 0 ]; then
  log "No new jobs — skipping scoring/build/alerts; jumping to seen + git."
  SKIP_TO_SEEN=1
else
  SKIP_TO_SEEN=0
fi

if [ "$SKIP_TO_SEEN" = "0" ]; then
  # -------------------------------------------------------------------------
  # Step 3 — Chunk (title-rescore + triage bulk_3 vs LLM chunks)
  # -------------------------------------------------------------------------
  log "Step 3: chunk"
  python3 "$SCOUT_DIR/jobscout_prep.py" --stage chunk --run-dir run && set_step 3 || fail "step3_chunk"
  CHUNKS=$(python3 -c "import json;print(len(json.load(open('run/chunk_manifest.json')).get('chunks',[])))" 2>/dev/null || echo 0)
  log "Chunk manifest: $CHUNKS chunks"

  # -------------------------------------------------------------------------
  # Step 4 — Scoring (sequential per-chunk claude -p)
  # -------------------------------------------------------------------------
  if [ "${CHUNKS:-0}" -gt 0 ]; then
    log "Step 4: scoring $CHUNKS chunk(s) (sequential, no sub-agents)"
    python3 "$SCOUT_DIR/jobscout_llm_stage.py" score --run-dir run --concurrency 1
    MISSING=$(python3 -c "
import json, os
ch=json.load(open('run/chunk_manifest.json')).get('chunks',[])
print(sum(1 for c in ch if not os.path.exists(c.get('output_path',''))))
" 2>/dev/null || echo 99)
    if [ "${MISSING:-99}" = "0" ]; then set_step 4; log "Scoring complete."; else fail "step4_scoring"; log "Scoring incomplete: $MISSING missing."; fi
  else
    log "No chunks to score."; set_step 4
  fi

  # -------------------------------------------------------------------------
  # Step 5 — Merge + build
  # -------------------------------------------------------------------------
  log "Step 5: merge + build"
  python3 "$SCOUT_DIR/jobscout_merge.py" --run-dir run --output run/scored.json \
    && python3 "$SCOUT_DIR/jobscout_build.py" --new run/scored.json --data data.json \
         --xlsx "$XLSX_PATH" --applied job-scout-applied.json \
    && set_step 5 || fail "step5_build"

  # -------------------------------------------------------------------------
  # Step 6 — Alert candidates (deterministic) + review (sequential claude -p)
  # -------------------------------------------------------------------------
  log "Step 6a: generate alert candidates"
  if python3 "$SCOUT_DIR/jobscout_alerts.py" \
        --data "$SCOUT_DIR/data.json" --state "$SCOUT_DIR/job-scout-state.json" \
        --applied "$SCOUT_DIR/job-scout-applied.json" \
        --config "$SCOUT_DIR/config/jobscout_config.json" \
        --out "$SCOUT_DIR/run/alert_candidates.json"; then
    PENDING=$(python3 -c "import json;print(sum(1 for x in json.load(open('run/alert_candidates.json')).get('candidates',[]) if x.get('alertApproved') is None))" 2>/dev/null || echo 0)
    log "Alert candidates needing review: $PENDING"
    if [ "${PENDING:-0}" -gt 0 ]; then
      log "Step 6b: reviewing $PENDING candidate(s) (sequential, no sub-agents)"
      python3 "$SCOUT_DIR/jobscout_llm_stage.py" review --run-dir run --concurrency 1
    fi
    python3 -c "
import json
p=json.load(open('run/alert_candidates.json')); c=p.get('candidates',[])
ap=sum(1 for x in c if x.get('alertApproved') is True); rj=sum(1 for x in c if x.get('alertApproved') is False)
s=json.load(open('run/run_summary.json')); s['alertReviewedCount']=len(c); s['alertRejectedCount']=rj; s['alertCount']=ap
json.dump(s, open('run/run_summary.json','w'), indent=2); print(f'Alert review: {ap} approved, {rj} rejected')
" && set_step 6 || fail "step6_reviewcount"

    # -----------------------------------------------------------------------
    # Step 7 — Slack alerts (single claude -p, Slack MCP) + mark alerted
    # -----------------------------------------------------------------------
    APPROVED=$(python3 -c "import json;print(sum(1 for x in json.load(open('run/alert_candidates.json')).get('candidates',[]) if x.get('alertApproved') is True))" 2>/dev/null || echo 0)
    if [ -z "$SLACK_CHANNEL" ]; then
      log "Slack not configured — skipping (alerts are in the dashboard)"
    elif [ "${APPROVED:-0}" -gt 0 ]; then
      SLACK_PROMPT="You are a non-interactive stage runner for the Job Scout alerts pipeline. Working dir: ${SCOUT_DIR}. Do ONLY Slack posting. Read run/alert_candidates.json. Post to Slack channel ${SLACK_CHANNEL} (via the Slack MCP tool) one message per job where ALL are true: alertApproved==true, _score>=7, geoFit==true, applied==false, caExcluded==false, and (remote jobs: salary>=143000 OR salary unknown/contract; OC jobs: no salary threshold). Format each message exactly:\n[Score N] Job Title — Company\nLocation | Salary\n<1-line reason>\n<Job URL>\nPost highest scores first. To stay idempotent across tiers, read run/run_summary.json 'slackPosted' before each score tier (score9/score8/score7); skip a tier already true; after posting a tier set it true and write run_summary.json. Do everything in THIS turn — no background, no wakeups, do not yield. Finish by printing: SLACK_DONE <count> posted."
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

# ---------------------------------------------------------------------------
# Step 8 — Gmail applied-status sync (single claude -p, Gmail MCP; best-effort)
# ---------------------------------------------------------------------------
if [ "$GMAIL_SYNC" = "True" ]; then
  log "Step 8: Gmail applied-status sync"
  GMAIL_PROMPT="You are a non-interactive stage runner for the Job Scout alerts pipeline. Working dir: ${SCOUT_DIR}. Do ONLY the Gmail status-sync. Read job-scout-applied.json. For each entry whose status is 'applied' or 'interviewing' (or status absent), run a targeted Gmail search (via the Gmail MCP tool) for that company + role, in batches of 5 to avoid rate limits. Read the most recent matching thread and classify it as one of: interview_invite, rejection, offer, or none. Do NOT auto-change 'status'. Instead, for any entry classified interview_invite/rejection/offer, add or update a 'statusHint' field on that EXISTING applied.json entry. Never add new entries, never remove entries; write all hint changes in a SINGLE write at the end. Do everything in THIS turn — no sub-agents, no background, no wakeups. Finish by printing: GMAIL_DONE <hints updated>."
  run_agent_stage "gmail-sync" "$GMAIL_PROMPT" || fail "step8_gmail"
else
  log "Gmail sync disabled"
fi
set_step 8

# Counts before Step 9 (seen) rewrites the summary in its own format.
NEW=$(python3 -c "import json;s=json.load(open('run/run_summary.json'));print(s.get('newCount', s.get('scrapedCount','?')))" 2>/dev/null || echo '?')
AL=$(python3 -c "import json;s=json.load(open('run/run_summary.json'));print(s.get('alertCount','?'))" 2>/dev/null || echo '?')

# ---------------------------------------------------------------------------
# Step 9 — Save seen IDs (deterministic; always runs)
# ---------------------------------------------------------------------------
log "Step 9: save seen IDs"
python3 "$SCOUT_DIR/jobscout_seen.py" \
  --seen "$SEEN_PATH" --data "$SCOUT_DIR/data.json" \
  --config "$SCOUT_DIR/config/jobscout_config.json" \
  --run-dir run --run-summary-out run/run_summary.json \
  && set_step 9 || fail "step9_seen"

# ---------------------------------------------------------------------------
# Step 10 — Archive + finalize
# ---------------------------------------------------------------------------
mkdir -p run_summaries
cp run/run_summary.json "run_summaries/run_summary_alerts_$(date +%Y-%m-%d_%H%M%S).json" 2>/dev/null || true
python3 - <<'PY'
import json, time
s=json.load(open("run/run_summary.json")); s["finishedAt"]=time.strftime("%Y-%m-%dT%H:%M:%S%z")
if s.get("status")!="partial": s["status"]="success"
json.dump(s, open("run/run_summary.json","w"), indent=2)
print("Final status:", s["status"], "| failed:", s.get("failedSteps"))
PY

# ---------------------------------------------------------------------------
# Step 10b — Keyword relevance report (deterministic; rolling 30d LinkedIn window)
# ---------------------------------------------------------------------------
log "Step 10b: keyword relevance report"
python3 "$SCOUT_DIR/jobscout_keyword_report.py" --source linkedin \
  --since "$(date -v-30d +%Y-%m-%d 2>/dev/null || date -d '30 days ago' +%Y-%m-%d)" \
  --out reports/keyword_relevance.md >/dev/null 2>&1 || log "keyword report failed (non-fatal)"

# ---------------------------------------------------------------------------
# Step 11 — Git commit + push
# ---------------------------------------------------------------------------
log "Step 11: git commit + push (${NEW} new, ${AL} alerts)"
[ -f .git/index.lock ] && mv .git/index.lock .git/index.lock.stale 2>/dev/null || true
git add \
  data.json job-scout-seen.json job-scout-state.json job-scout-applied.json \
  run/run_summary.json run_summaries/ reports/ 2>/dev/null || true
git -c user.name="Job Scout" -c user.email="scout@local" \
  commit -m "job-scout-alerts run $(date +%Y-%m-%d): ${NEW} new jobs, ${AL} alerts" \
  || echo "Nothing to commit."
if git remote get-url origin >/dev/null 2>&1; then
  git push origin HEAD || echo "git push failed (committed locally; next run catches up)."
else
  log "no git remote — skipping push (local commits only)"
fi

log "=== ALERTS pipeline finished ($(date '+%Y-%m-%d %H:%M:%S %Z')) ==="
echo "log: $LOG"
