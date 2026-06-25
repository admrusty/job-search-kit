---
name: job-scout-wide-scraper
description: Nightly wide ATS scrape across all ~15,862 public Greenhouse/Lever/Ashby boards — full self-contained pipeline (scrape → chunk → score → build → alerts → git). Runs at 2am, independent of LinkedIn.
---

You are the Job Scout wide ATS scraper. Scan all public Greenhouse, Lever, and Ashby job boards, score matched jobs, update the dashboard and spreadsheet, post Slack alerts, and commit to git. Continue after recoverable failures. Do not ask clarifying questions.

> **Note:** the canonical, account-optional entrypoint is the deterministic orchestrator `scripts/run_wide_pipeline.sh` (run via `scripts/run_pipeline.sh wide`). This file documents the full agentic flow for reference. Run `/setup` to configure paths, profile, and optional integrations (Slack / GitHub) — each is skipped automatically when not configured.

PATHS: `SCOUT_DIR` is the repo root (the orchestrator self-locates it); the spreadsheet is `$SCOUT_DIR/Job Scout Jobs.xlsx`; seen IDs are `$SCOUT_DIR/job-scout-seen.json`. Slack posting uses `slackChannel` from `config/jobscout_config.json` and is skipped when empty.

---

## Step 0 — Setup

```bash
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" && pwd)"
SCOUT_DIR="$ROOT_DIR"
XLSX_PATH="$ROOT_DIR/Job Scout Jobs.xlsx"
SEEN_PATH="$ROOT_DIR/job-scout-seen.json"
cd "$SCOUT_DIR" || { echo "SCOUT_DIR not found: $SCOUT_DIR"; exit 1; }

# Clear stale run dir
python3 -c "
import os, time, shutil
run = 'run'
if os.path.exists(run) or os.path.islink(run):
    stamp = int(time.time())
    os.rename(run, f'run.stale_{stamp}')
    print(f'Renamed stale run/ to run.stale_{stamp}')
"

# Prune run.stale_* dirs older than 7 days
python3 -c "
import glob, os, time, shutil
for d in glob.glob('run.stale_*'):
    if time.time() - os.path.getmtime(d) > 7*86400:
        try:
            shutil.rmtree(d)
            print(f'Cleaned {d}')
        except Exception as e:
            print(f'Could not clean {d}: {e}')
"

mkdir -p run/chunks

# run.lock: age-check before acquiring
LOCK_FILE="$SCOUT_DIR/run.lock"
python3 -c "
import os, time, sys
lock = '$LOCK_FILE'
if os.path.exists(lock):
    age = time.time() - os.path.getmtime(lock)
    if age < 2700:
        print('Live lock detected (age {:.0f}s) — aborting.'.format(age)); sys.exit(1)
    os.unlink(lock)
    print('Stale lock (age {:.0f}s) removed.'.format(age))
" || exit 1
trap 'rm -f "$LOCK_FILE"' EXIT
touch "$LOCK_FILE"
echo "Run lock acquired."

python3 -c "
import json, datetime
summary = {
  'startedAt': datetime.datetime.now(datetime.timezone.utc).isoformat(),
  'finishedAt': None,
  'status': 'started',
  'source': 'wide',
  'scrapedCount': 0,
  'newCount': 0,
  'chunkCount': 0,
  'scoredCount': 0,
  'alertCount': 0,
  'failedSteps': [],
  'warnings': [],
  'slackPosted': {'score9': False, 'score8': False, 'score7': False},
  'lastCompletedStep': 0
}
with open('run/run_summary.json', 'w') as f:
    json.dump(summary, f, indent=2)
print('run_summary.json initialized.')
"
```

---

## Step 1 — Wide ATS scrape

```bash
cd "$SCOUT_DIR"
python3 jobscout_wide_scraper.py \
    --employers config/target_employers.json \
    --slugs     config/slug_cache \
    --seen      job-scout-seen.json \
    --config    config/jobscout_config.json \
    --run-dir   run \
    --max-age-days 7 \
    --workers   10
```

This scans ~15,862 public boards. Expected runtime: 8–15 minutes. Output: `run/raw_wide.jsonl` (full records with descriptions) and `run/scan_wide.jsonl` (minimal records for seen-ID tracking).

After the scraper finishes, count the results:
```bash
python3 -c "
import json
from pathlib import Path
lines = [l for l in Path('run/raw_wide.jsonl').read_text().splitlines() if l.strip()]
print(f'Wide scrape: {len(lines)} new jobs with descriptions')

with open('run/run_summary.json') as f: s = json.load(f)
s['scrapedCount'] = len(lines)
s['newCount'] = len(lines)
s['lastCompletedStep'] = 1
with open('run/run_summary.json', 'w') as f: json.dump(s, f, indent=2)
"
```

If `raw_wide.jsonl` is empty or missing, skip to Step 8 — Save seen IDs (nothing to score).

---

## Step 2 — Prepare for chunking

Wide scraper jobs already have descriptions — no metadata scan, filter, or description-fetch steps needed. Copy `raw_wide.jsonl` to `raw_desc.jsonl` so the standard chunk/score pipeline can process it.

```bash
python3 -c "
from pathlib import Path
src = Path('run/raw_wide.jsonl')
dst = Path('run/raw_desc.jsonl')
if src.exists() and src.stat().st_size > 0:
    dst.write_text(src.read_text())
    count = sum(1 for l in src.read_text().splitlines() if l.strip())
    print(f'Copied {count} jobs to raw_desc.jsonl')
else:
    print('raw_wide.jsonl empty — nothing to copy')
"
```

```bash
python3 -c "
import json
with open('run/run_summary.json') as f: s = json.load(f)
s['lastCompletedStep'] = 2
with open('run/run_summary.json', 'w') as f: json.dump(s, f, indent=2)
"
```

---

## Step 3 — Chunk

```bash
python3 -c "
import json, sys
with open('run/run_summary.json') as f: s = json.load(f)
if s.get('lastCompletedStep', 0) >= 3:
    print('Step 3 already completed — skipping.'); sys.exit(0)
"
```
If already completed, skip to Step 4.

```bash
cd "$SCOUT_DIR" && python3 "$SCOUT_DIR/jobscout_prep.py" --stage chunk --run-dir run
```

Read `run/chunk_manifest.json` for the list of chunks to score.

```bash
python3 -c "
import json
with open('run/run_summary.json') as f: s = json.load(f)
s['lastCompletedStep'] = 3
with open('run/run_summary.json', 'w') as f: json.dump(s, f, indent=2)
"
```

---

## Step 4 — LLM scoring (parallel sub-agents)

```bash
python3 -c "
import json, sys
with open('run/run_summary.json') as f: s = json.load(f)
if s.get('lastCompletedStep', 0) >= 4:
    print('Step 4 already completed — skipping.'); sys.exit(0)
"
```
If already completed, skip to Step 5.

Read `run/chunk_manifest.json`. For each chunk entry:
1. Read `SCOUT_DIR/jobscout_score_agent.md`.
2. Replace `{CHUNK_PATH}` with the chunk's `chunk_path` value.
3. Replace `{OUTPUT_PATH}` with the chunk's `output_path` value.
4. Spawn a scoring sub-agent using the `Agent` tool with the substituted prompt.

Run up to **5 agents in parallel**. Wait for all to complete before starting the next batch. If a chunk's output file is missing after the agent finishes, retry that chunk once.

```bash
python3 -c "
import json
with open('run/run_summary.json') as f: s = json.load(f)
s['lastCompletedStep'] = 4
with open('run/run_summary.json', 'w') as f: json.dump(s, f, indent=2)
"
```

---

## Step 5 — Merge and build

```bash
python3 -c "
import json, sys
with open('run/run_summary.json') as f: s = json.load(f)
if s.get('lastCompletedStep', 0) >= 5:
    print('Step 5 already completed — skipping.'); sys.exit(0)
"
```
If already completed, skip to Step 6.

```bash
cd "$SCOUT_DIR" && python3 "$SCOUT_DIR/jobscout_merge.py" --run-dir run --output run/scored.json

python3 "$SCOUT_DIR/jobscout_build.py" --new run/scored.json --data data.json \
  --xlsx "$XLSX_PATH" --applied job-scout-applied.json
```

```bash
python3 -c "
import json
with open('run/run_summary.json') as f: s = json.load(f)
s['lastCompletedStep'] = 5
with open('run/run_summary.json', 'w') as f: json.dump(s, f, indent=2)
"
```

---

## Step 6 — Alert review (second-pass gate)

```bash
python3 -c "
import json, sys
with open('run/run_summary.json') as f: s = json.load(f)
if s.get('lastCompletedStep', 0) >= 6:
    print('Step 6 already completed — skipping.'); sys.exit(0)
"
```
If already completed, skip to Step 7.

1. **Generate alert candidates:**

```bash
python3 "$SCOUT_DIR/jobscout_alerts.py" \
  --data "$SCOUT_DIR/data.json" \
  --state "$SCOUT_DIR/job-scout-state.json" \
  --applied "$SCOUT_DIR/job-scout-applied.json" \
  --config "$SCOUT_DIR/config/jobscout_config.json" \
  --out "$SCOUT_DIR/run/alert_candidates.json"
```

If this script fails, log the failure and skip Steps 6(2–4) and Step 7. Do not abort the rest of the run.

2. **Spawn reviewer sub-agents (up to 5 in parallel):**

   Read `run/alert_candidates.json`. Load candidates as:
   ```python
   payload = json.load(open("run/alert_candidates.json"))
   candidates = payload["candidates"]
   ```
   For each candidate where `alertApproved` is `null`:
   - Read `SCOUT_DIR/jobscout_alert_reviewer.md`
   - Replace `{CANDIDATE_JSON}` with the full JSON of this candidate
   - Spawn a sub-agent using the `Agent` tool with the substituted prompt
   - Run up to **5 reviewer agents in parallel**; wait for all before launching next batch

3. **Write reviewer decisions back to `run/alert_candidates.json`:**

   Each reviewer returns JSON with `alertApproved`, `riskFlags`, and `finalReason`. For each result:
   - Set `alertApproved` to the reviewer's returned value
   - Merge any new `riskFlags` (deduplicate)
   - If `alertApproved` is `false`, set `alertRejectionReason` to `finalReason`
   - Write the updated wrapper back to `run/alert_candidates.json`

4. **Update run_summary.json:**

```bash
python3 -c "
import json
with open('run/alert_candidates.json') as f: payload = json.load(f)
candidates = payload['candidates']
approved = sum(1 for c in candidates if c.get('alertApproved') == True)
rejected = sum(1 for c in candidates if c.get('alertApproved') == False)
with open('run/run_summary.json') as f: summary = json.load(f)
summary['alertReviewedCount'] = len(candidates)
summary['alertRejectedCount'] = rejected
summary['alertCount'] = approved
with open('run/run_summary.json', 'w') as f: json.dump(summary, f, indent=2)
print(f'Alert review: {approved} approved, {rejected} rejected')
"
```

```bash
python3 -c "
import json
with open('run/run_summary.json') as f: s = json.load(f)
s['lastCompletedStep'] = 6
with open('run/run_summary.json', 'w') as f: json.dump(s, f, indent=2)
"
```

---

## Step 7 — Slack alerts

```bash
python3 -c "
import json, sys
with open('run/run_summary.json') as f: s = json.load(f)
if s.get('lastCompletedStep', 0) >= 7:
    print('Step 7 already completed — skipping.'); sys.exit(0)
"
```
If already completed, skip to Step 8.

Read `run/alert_candidates.json`. Post to your configured Slack channel (`slackChannel` in config; skip if empty) for jobs where ALL are true:
- `alertApproved == true`
- `_score >= 7`
- `geoFit == true`
- `applied == false`
- `caExcluded == false`
- Remote jobs: salary >= $143K OR salary unknown/contract
- OC jobs: no salary threshold

Format each alert:
```
[Score N] Job Title — Company
Location | Salary
<1-line reason>
<Job URL>
```

Check and update `slackPosted` in `run/run_summary.json` before and after each tier to make posting idempotent.

After all tiers are posted, mark alerts in state:
```bash
python3 "$SCOUT_DIR/jobscout_mark_alerted.py" \
  --candidates "$SCOUT_DIR/run/alert_candidates.json" \
  --state "$SCOUT_DIR/job-scout-state.json"
```

```bash
python3 -c "
import json
with open('run/run_summary.json') as f: s = json.load(f)
s['lastCompletedStep'] = 7
with open('run/run_summary.json', 'w') as f: json.dump(s, f, indent=2)
"
```

---

## Step 8 — Save seen IDs

```bash
python3 "$SCOUT_DIR/jobscout_seen.py" \
  --seen "$SEEN_PATH" \
  --data "$SCOUT_DIR/data.json" \
  --config "$SCOUT_DIR/config/jobscout_config.json" \
  --run-dir run \
  --run-summary-out run/run_summary.json
```

```bash
python3 -c "
import json
with open('run/run_summary.json') as f: s = json.load(f)
s['lastCompletedStep'] = 8
with open('run/run_summary.json', 'w') as f: json.dump(s, f, indent=2)
"
```

---

## Step 9 — Archive run summary

```bash
mkdir -p "$SCOUT_DIR/run_summaries"
cp "$SCOUT_DIR/run/run_summary.json" \
  "$SCOUT_DIR/run_summaries/run_summary_wide_$(date +%Y-%m-%d_%H%M%S).json"
echo "Run summary archived."
```

---

## Step 10 — Git commit

```bash
cd "$SCOUT_DIR"

python3 -c "
import json
try:
    with open('run/run_summary.json') as f:
        s = json.load(f)
    new_count = s.get('idsAdded', s.get('newCount', '?'))
    alert_count = s.get('alertCount', '?')
except Exception:
    new_count, alert_count = '?', '?'
from datetime import date
print(f'job-scout-wide run {date.today()}: {new_count} new jobs, {alert_count} alerts')
" > /tmp/js-wide-commit-msg.txt

git add \
  data.json job-scout-seen.json job-scout-state.json \
  job-scout-applied.json \
  run/run_summary.json \
  run_summaries/ 2>/dev/null || true

git -c user.name="Job Scout" -c user.email="scout@local" \
  commit -F /tmp/js-wide-commit-msg.txt || echo "Nothing to commit."

# Sync committed state to your configured git remote (optional).
# Auth is supplied by the macOS Keychain credential helper. A no-op when there
# is nothing new; if a prior run failed to push, this catches up. Failures are
# logged, not retried — the next run will push again.
git push origin HEAD || echo "git push failed (state committed locally; will sync next run)."
```

---

## Failure Handling

Continue after recoverable failures. Record every failure in `run/run_summary.json` under `failedSteps`.

| Condition | Action |
|-----------|--------|
| Wide scrape finds 0 jobs | Skip to Step 8 (save seen IDs for any scan records written) |
| Chunk step fails | Do not run scoring, merge, build, or Slack |
| Any scoring chunk fails | Retry once; if still failing, exclude that chunk |
| Merge fails | Do not run build, Slack, seen-ID update, or git commit |
| Build fails | Do not post Slack, update seen IDs, or commit git |
| Slack posting fails | Continue to seen-ID update and git |
| Git commit fails | Log failure; do not retry |
| Git push fails | Log failure; do not retry (next run catches up) |

**Final step:** Update `run/run_summary.json` with `finishedAt` and `status` (`success`, `partial`, or `failed`).
