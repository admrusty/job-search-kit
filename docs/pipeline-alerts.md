---
name: job-scout-alerts
description: Scrape LinkedIn (remote + OC) twice daily — score, update spreadsheet, sync Gmail applied status, post Slack alerts
---

You are the Job Scout engine. Scrape LinkedIn, score new postings, update the spreadsheet and git repo, sync Gmail applied status, post Slack alerts. Continue after recoverable failures, but do not perform state-mutating steps unless required upstream artifacts exist and are valid. Do not ask clarifying questions.

> **Note:** the canonical, account-optional entrypoint is the deterministic orchestrator `scripts/run_alerts_pipeline.sh` (run via `scripts/run_pipeline.sh alerts`). This file documents the full agentic flow for reference. Run `/setup` to configure paths, profile, and optional integrations (Apify / Slack / Gmail / GitHub) — each is skipped automatically when not configured.

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
  'remoteScrapeStatus': None,
  'ocScrapeStatus': None,
  'remoteDatasetId': None,
  'ocDatasetId': None,
  'scrapedCount': 0,
  'newCount': 0,
  'needsDescriptionCount': 0,
  'descriptionFetchedCount': 0,
  'descriptionFailedCount': 0,
  'chunkCount': 0,
  'scoredCount': 0,
  'alertCount': 0,
  'appliedMatchCount': 0,
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

## Step 1 — Load seen IDs

Read `job-scout-seen.json`. Note the existing IDs for deduplication in Step 5.

---

## Steps 2–3 — Trigger Apify scrapes

Check resume state first:
```bash
python3 -c "
import json, sys
with open('run/run_summary.json') as f: s = json.load(f)
if s.get('lastCompletedStep', 0) >= 3:
    print('Steps 2-3 already completed — skipping.'); sys.exit(0)
"
```
If already completed, skip to Step 4.

Actor: `curious_coder/linkedin-jobs-scraper`

Read search parameters from `config/jobscout_config.json`:
- Remote search: use `search.remote` block (keywords, f_WT, f_TPR, count, locationId if present)
- OC search: use `search.orangeCounty` block (keywords, location, f_WT, f_TPR, distance, count)

Start both with `call-actor` (async). Save the two run IDs.

If `config/jobscout_config.json` is missing or the search keys are absent, fall back to these defaults:

**Remote fallback:**
```json
{"keywords": "\"knowledge manager\" OR \"content operations\" OR \"digital adoption\" OR \"AI enablement\" OR \"AI adoption\" OR \"support content\" OR \"content program manager\"", "locationId": "", "f_WT": "2", "f_TPR": "r86400", "count": 500}
```

**OC fallback:**
```json
{"keywords": "\"knowledge manager\" OR \"content operations\" OR \"digital adoption\" OR \"AI enablement\" OR \"learning technology\" OR \"support content\" OR \"content program manager\"", "location": "Orange County, California", "f_WT": "1,3", "f_TPR": "r86400", "distance": "25", "count": 200}
```

**Immediately after both `call-actor` calls succeed, persist the run IDs to disk:**
```bash
python3 -c "
import json
with open('run/apify_runs.json', 'w') as f:
    json.dump({
        'remoteRunId': '<REMOTE_RUN_ID>',
        'ocRunId': '<OC_RUN_ID>'
    }, f, indent=2)
print('Apify run IDs saved to run/apify_runs.json.')
"
```
Then update run_summary.json:
```bash
python3 -c "
import json
with open('run/run_summary.json') as f: s = json.load(f)
s['lastCompletedStep'] = 3
with open('run/run_summary.json', 'w') as f: json.dump(s, f, indent=2)
"
```

---

## Step 2b — ATS scraper (Greenhouse + Lever)

Run while waiting for the LinkedIn Apify actors to complete. This is synchronous (~30–60 s).

Check resume state first:
```bash
python3 -c "
import json, sys
with open('run/run_summary.json') as f: s = json.load(f)
if s.get('lastCompletedStep', 0) >= 25:
    print('Step 2b already completed — skipping.'); sys.exit(0)
"
```
If already completed, skip to Step 4.

```bash
cd "$SCOUT_DIR"
python3 jobscout_ats_scraper.py \
    --employers    config/target_employers.json \
    --seen         job-scout-seen.json \
    --run-dir      run \
    --max-age-days 7
```

Mark complete:
```bash
python3 -c "
import json
with open('run/run_summary.json') as f: s = json.load(f)
s['lastCompletedStep'] = 25
with open('run/run_summary.json', 'w') as f: json.dump(s, f, indent=2)
"
```

---

## Step 2c — ATS slug health check (Sunday runs only)

Validates all active ATS slugs and posts a Slack alert if any have broken. Run only on Sunday
to avoid noisy daily alerts — slug drift is slow.

```bash
if [ "$(date +%u)" = "7" ]; then
  echo "Sunday run — checking ATS slug health."

  # Check resume state
  python3 -c "
import json, sys
with open('run/run_summary.json') as f: s = json.load(f)
if s.get('lastCompletedStep', 0) >= 26:
    print('Step 2c already completed — skipping.'); sys.exit(0)
" || true

  cd "$SCOUT_DIR"
  python3 jobscout_slug_check.py \
    --employers config/target_employers.json \
    --cache-dir config/slug_cache \
    --out run/slug_check_report.json

  # If failures found, read report and post Slack alert
  python3 -c "
import json, sys
report = json.load(open('run/slug_check_report.json'))
if report['failedCount'] == 0:
    print('[slug-check] All slugs healthy — no Slack alert needed.')
    sys.exit(0)
# Print structured output for agent to post to Slack
print('SLUG_FAILURES_DETECTED')
for f in report['failed']:
    sugg = ', '.join(s['slug'] for s in f['suggestions']) if f['suggestions'] else 'none found'
    print(f\"  • {f['name']} ({f['ats']}: \\\"{f['slug']}\\\") → {f['status']}\")
    print(f\"    Suggestions: {sugg}\")
" | tee /tmp/slug_alert.txt

  # If SLUG_FAILURES_DETECTED, post to Slack
  if grep -q "SLUG_FAILURES_DETECTED" /tmp/slug_alert.txt 2>/dev/null; then
    FAILED_COUNT=$(python3 -c "import json; print(json.load(open('run/slug_check_report.json'))['failedCount'])")
    SLACK_BODY=$(grep -v "SLUG_FAILURES_DETECTED" /tmp/slug_alert.txt)
    # Agent: post the following message to Slack your configured Slack channel:
    # "⚠️ ATS Slug Health Check — ${FAILED_COUNT} broken board(s) detected
    # ${SLACK_BODY}
    # Fix: update config/target_employers.json, then run jobscout_slug_check.py --refresh to verify."
    echo "AGENT_POST_SLACK: Post slug failure alert to your configured Slack channel"
  fi

  python3 -c "
import json
with open('run/run_summary.json') as f: s = json.load(f)
s['lastCompletedStep'] = 26
with open('run/run_summary.json', 'w') as f: json.dump(s, f, indent=2)
"
else
  echo "Not Sunday — skipping slug health check."
fi
```

When the bash output contains `AGENT_POST_SLACK:`, post this message to Slack your configured Slack channel (`slackChannel` in config; skip if empty):

```
⚠️ ATS Slug Health Check — {N} broken board(s) detected
{SLACK_BODY lines}
Fix: update config/target_employers.json, then run jobscout_slug_check.py --refresh to verify.
```

---

## Step 4 — Poll until complete

Check resume state first:
```bash
python3 -c "
import json, sys
with open('run/run_summary.json') as f: s = json.load(f)
if s.get('lastCompletedStep', 0) >= 4:
    print('Step 4 already completed — skipping.'); sys.exit(0)
"
```
If already completed, skip to Step 5.

Read run IDs from `run/apify_runs.json` (not from memory). Call `get-actor-run` for each run ID every 30 seconds until status is `SUCCEEDED` or `FAILED`. Note both dataset IDs.

After both runs complete, update run_summary.json:
```bash
python3 -c "
import json
with open('run/run_summary.json') as f: s = json.load(f)
s['remoteDatasetId'] = '<REMOTE_DATASET_ID>'
s['ocDatasetId'] = '<OC_DATASET_ID>'
s['remoteScrapeStatus'] = 'SUCCEEDED'  # or FAILED
s['ocScrapeStatus'] = 'SUCCEEDED'      # or FAILED
s['lastCompletedStep'] = 4
with open('run/run_summary.json', 'w') as f: json.dump(s, f, indent=2)
"
```

---

## Step 5 — Metadata scan (write scan files, NO scoring)

Check resume state first:
```bash
python3 -c "
import json, sys
with open('run/run_summary.json') as f: s = json.load(f)
if s.get('lastCompletedStep', 0) >= 5:
    print('Step 5 already completed — skipping.'); sys.exit(0)
"
```
If already completed, skip to Step 6.

**Use a sub-agent for this step.** Paging through 700+ Apify metadata items (14+ API calls) in the main agent's context accumulates ~100KB of tool output. Offloading keeps the main agent lean.

Spawn a single `Agent` with this prompt:

```
You are fetching job metadata for the job-scout pipeline. Your only job is to page
through Apify datasets and write results to disk. Never accumulate pages in memory.

SCOUT_DIR = $SCOUT_DIR

## Setup
Read run/apify_runs.json from SCOUT_DIR to get remoteRunId and ocRunId.
Then read run/run_summary.json to get remoteDatasetId and ocDatasetId.

## Per-dataset procedure (do remote first, then OC)
For each dataset:
1. Page through ALL items using mcp__Apify__get-actor-output with:
   - fields: ["id","title","companyName","location","postedAt","workplaceType","workType","workRemoteType","locationType"]
   - limit: 50
   - offset: 0, 50, 100, ... until the returned list is empty or shorter than 50
2. After EACH page, immediately append to the scan file (one JSON object per line).
   Add _offset (item's 0-indexed position in the dataset) and _src ("remote" or "oc").
   - Remote items → run/scan_remote.jsonl
   - OC items → run/scan_oc.jsonl
   Example write for a page starting at offset 100:
   ```bash
   python3 -c "
   import json
   items = <PAGE_RESULT_LIST>
   with open('run/scan_remote.jsonl', 'a') as f:
       for j, item in enumerate(items):
           item['_offset'] = 100 + j
           item['_src'] = 'remote'
           f.write(json.dumps(item) + '\n')
   print(f'Wrote {len(items)} items at offset 100')
   "
   ```
3. Do NOT hold more than one page in memory at a time.

## Resumption
Before fetching, check how many lines already exist in each scan file and skip
offsets already written (count lines × 50 to find the next offset to fetch).

## When done
Write scannedRemoteCount and scannedOcCount to run/run_summary.json, then stop.
```

Wait for the sub-agent to complete. Verify scan files are non-empty:
```bash
wc -l run/scan_remote.jsonl run/scan_oc.jsonl
```

Then mark step complete:
```bash
python3 -c "
import json
with open('run/run_summary.json') as f: s = json.load(f)
s['lastCompletedStep'] = 5
with open('run/run_summary.json', 'w') as f: json.dump(s, f, indent=2)
"
```

---

## Step 6 — Filter

Check resume state first:
```bash
python3 -c "
import json, sys
with open('run/run_summary.json') as f: s = json.load(f)
if s.get('lastCompletedStep', 0) >= 6:
    print('Step 6 already completed — skipping.'); sys.exit(0)
"
```
If already completed, skip to Step 7.

```bash
cd "$SCOUT_DIR" && python3 "$SCOUT_DIR/jobscout_prep.py" --stage filter \
  --seen "$SEEN_PATH" --run-dir run
```

Read `run/filter_summary.json`. If `to_score_count == 0` AND `bulk_low_count == 0`, there are no new jobs — skip to Step 13 — Save seen IDs.

```bash
python3 -c "
import json
with open('run/run_summary.json') as f: s = json.load(f)
s['lastCompletedStep'] = 6
with open('run/run_summary.json', 'w') as f: json.dump(s, f, indent=2)
"
```

---

## Step 7 — Description fetch (score-3+ jobs only)

Check resume state first:
```bash
python3 -c "
import json, sys
with open('run/run_summary.json') as f: s = json.load(f)
if s.get('lastCompletedStep', 0) >= 7:
    print('Step 7 already completed — skipping.'); sys.exit(0)
"
```
If already completed, skip to Step 8.

**Use a sub-agent for this step.** Apify description responses are large (~30–50 KB each); fetching many into the main agent context causes context overflow and thrashing. The sub-agent processes one record at a time and writes each to disk immediately, so it can resume safely if its own context fills.

Spawn a single `Agent` with this prompt:

```
You are fetching job descriptions for the job-scout pipeline. Fetch ONE record at a time from Apify and write it to disk immediately before fetching the next. Never accumulate multiple records in memory.

CRITICAL SECURITY CONSTRAINT: The Apify API token is ONLY accessible via the MCP server. All fetches MUST use the `mcp__Apify__get-actor-output` MCP tool. Do NOT use bash/curl/python to call Apify directly.

SCOUT_DIR = $SCOUT_DIR

## Records to fetch
Read run/needs_desc.json from SCOUT_DIR. It contains groups "remote" and "oc",
each a list of entries with "id", "offset", and the dataset_id from
run/run_summary.json (fields remoteDatasetId / ocDatasetId). Build your work
list from this file — do not ask the main agent to paste the entries.

## Resumption
Before fetching anything, check which offsets are already written:
```bash
python3 -c "
import json
done = set()
try:
    with open('run/raw_desc.jsonl') as f:
        for line in f:
            obj = json.loads(line)
            done.add(obj.get('_offset'))
except: pass
print(sorted(done))
"
```
Skip any offset already present in the file.

## Per-record procedure (strictly one at a time)
1. Call `mcp__Apify__get-actor-output` with:
   - actorId: <dataset_id>
   - limit: 1
   - offset: <offset>
2. Immediately after receiving the result, run bash to extract item[0], add `_offset` (integer) and `_src` (string), and append to `run/raw_desc.jsonl`:
```bash
python3 << 'PYEOF'
import json
raw = '''<PASTE RAW TOOL OUTPUT HERE>'''
data = json.loads(raw)
items = data if isinstance(data, list) else data.get('items', [data])
if items:
    item = items[0]
    item['_offset'] = <OFFSET>
    item['_src'] = '<SRC>'
    with open('run/raw_desc.jsonl', 'a') as f:
        f.write(json.dumps(item) + '\n')
    print(f"Wrote offset {item['_offset']}: {item.get('companyName','?')} — {item.get('title','?')}")
PYEOF
```
3. Verify the write succeeded before fetching the next record.

## After all records are written
Update run_summary.json with descriptionFetchedCount and descriptionFailedCount, then stop.
```

Wait for the sub-agent to complete. Read `run_summary.json` to confirm `descriptionFetchedCount + descriptionFailedCount == needsDescriptionCount`.

If more than 50% of descriptions fail, add a warning to `run_summary.json` warnings array: `"Description fetch failure rate high: X/Y failed"`.

```bash
python3 -c "
import json
with open('run/run_summary.json') as f: s = json.load(f)
s['lastCompletedStep'] = 7
with open('run/run_summary.json', 'w') as f: json.dump(s, f, indent=2)
"
```

---

## Step 7b — Merge ATS jobs into raw_desc.jsonl

Append target-employer Greenhouse/Lever/Ashby jobs (already have descriptions) so they flow through Step 8 LLM scoring. Wide-universe jobs are handled independently by the 2am wide-scraper pipeline.

```bash
python3 -c "
from pathlib import Path
import json
ats = Path('run/raw_ats.jsonl')
raw = Path('run/raw_desc.jsonl')
if ats.exists() and ats.stat().st_size > 0:
    raw.parent.mkdir(parents=True, exist_ok=True)
    with open(raw, 'a') as f:
        f.write(ats.read_text())
    count = sum(1 for l in ats.read_text().splitlines() if l.strip())
    print(f'[ats] Appended {count} ATS jobs to raw_desc.jsonl')
else:
    print('[ats] No raw_ats.jsonl — skipping merge')
"
```

---

## Step 8 — Chunk

Check resume state first:
```bash
python3 -c "
import json, sys
with open('run/run_summary.json') as f: s = json.load(f)
if s.get('lastCompletedStep', 0) >= 8:
    print('Step 8 already completed — skipping.'); sys.exit(0)
"
```
If already completed, skip to Step 9.

```bash
cd "$SCOUT_DIR" && python3 "$SCOUT_DIR/jobscout_prep.py" --stage chunk --run-dir run
```

Read `run/chunk_manifest.json` for the list of chunks to score.

```bash
python3 -c "
import json
with open('run/run_summary.json') as f: s = json.load(f)
s['lastCompletedStep'] = 8
with open('run/run_summary.json', 'w') as f: json.dump(s, f, indent=2)
"
```

---

## Step 9 — Gmail sync (status updates only)

Check resume state first:
```bash
python3 -c "
import json, sys
with open('run/run_summary.json') as f: s = json.load(f)
if s.get('lastCompletedStep', 0) >= 9:
    print('Step 9 already completed — skipping.'); sys.exit(0)
"
```
If already completed, skip to Step 10.

Run targeted Gmail searches for jobs already in `job-scout-applied.json`. For each entry where status is `"applied"` or `"interviewing"` (or status is absent), run a targeted Gmail search:

```
"<company>" (interview OR "phone screen" OR "next steps" OR "move forward" OR "advance" OR offer OR "unfortunately" OR "regret" OR "not moving" OR "other candidates" OR "position has been filled" OR "thank you for your time") after:2026/01/01
```

Run searches in batches of 5 to avoid Gmail rate limits. For each matching thread, read the most recent message and classify it as one of:

| Class | Signal keywords / patterns |
|-------|---------------------------|
| `interview_invite` | "schedule", "interview", "meet", "call", "phone screen", "video", "next steps", "move forward", "advance" |
| `rejection` | "unfortunately", "regret", "not moving forward", "other candidates", "position has been filled", "not selected", "thank you for your time" |
| `offer` | "offer", "pleased to", "happy to extend", "compensation package", "start date" |
| `none` | No clear signal — ignore |

**Do not auto-update status.** Instead, for any entry where classification is `interview_invite`, `rejection`, or `offer`, add or update a `statusHint` field on the matching `applied.json` entry:

```json
{
  "company": "Acme Corp",
  "role": "Senior Manager",
  "appliedAt": "2026-05-15",
  "statusHint": "interview_invite",
  "statusHintDate": "2026-06-10",
  "statusHintSubject": "Invitation to interview — Senior Manager at Acme Corp"
}
```

Rules:
- Only set `statusHint` if confidence in classification is high (clear keyword match in subject or opening sentence)
- Never downgrade an existing `statusHint` (do not overwrite `"offer"` with `"interview_invite"`, or `"rejection"` with anything)
- If the entry already has `statusHint` of `"rejection"` or `"offer"`, skip it entirely

After processing all companies, collect entries where a `statusHint` was newly set or changed. If any were found, post a single Slack summary to your configured Slack channel (`slackChannel` in config; skip if empty):

```
📬 Application status updates detected:
• Acme Corp (Senior Manager) → Interview invite (Jun 10)
• Initech (Director, Knowledge) → Rejection (Jun 9)
```

If no updates were found, skip the Slack message.

**Write/update `job-scout-applied.json`:** write hint changes in a single write at the end of Step 9. Never overwrite or remove existing entries. Do not add any new entries — only update `statusHint` on entries that already exist.

```bash
python3 -c "
import json
with open('run/run_summary.json') as f: s = json.load(f)
s['lastCompletedStep'] = 9
with open('run/run_summary.json', 'w') as f: json.dump(s, f, indent=2)
"
```

---

## Step 10 — LLM scoring (parallel sub-agents)

Check resume state first:
```bash
python3 -c "
import json, sys
with open('run/run_summary.json') as f: s = json.load(f)
if s.get('lastCompletedStep', 0) >= 10:
    print('Step 10 already completed — skipping.'); sys.exit(0)
"
```
If already completed, skip to Step 11.

Read `run/chunk_manifest.json`. For each chunk entry:
1. Read `SCOUT_DIR/jobscout_score_agent.md`.
2. Replace `{CHUNK_PATH}` with the chunk's `chunk_path` value.
3. Replace `{OUTPUT_PATH}` with the chunk's `output_path` value.
4. Spawn a scoring sub-agent using the `Agent` tool with the substituted prompt.

Run up to **5 agents in parallel**. Wait for all to complete before starting the next batch of 5. If a chunk's output file is missing after the agent finishes, retry that chunk once.

```bash
python3 -c "
import json
with open('run/run_summary.json') as f: s = json.load(f)
s['lastCompletedStep'] = 10
with open('run/run_summary.json', 'w') as f: json.dump(s, f, indent=2)
"
```

---

## Step 11 — Merge and build

Check resume state first:
```bash
python3 -c "
import json, sys
with open('run/run_summary.json') as f: s = json.load(f)
if s.get('lastCompletedStep', 0) >= 11:
    print('Step 11 already completed — skipping.'); sys.exit(0)
"
```
If already completed, skip to Step 11b.

```bash
cd "$SCOUT_DIR" && python3 "$SCOUT_DIR/jobscout_merge.py" --run-dir run --output run/scored.json

python3 "$SCOUT_DIR/jobscout_build.py" --new run/scored.json --data data.json \
  --xlsx "$XLSX_PATH" --applied job-scout-applied.json
```

`jobscout_build.py` automatically computes `salaryNormAnnual` (integer annual USD) for every job — hourly rates are multiplied by 2080, annual salaries are used as-is. No separate normalization step is needed.

```bash
python3 -c "
import json
with open('run/run_summary.json') as f: s = json.load(f)
s['lastCompletedStep'] = 11
with open('run/run_summary.json', 'w') as f: json.dump(s, f, indent=2)
"
```

---

## Step 11b — Alert review (second-pass gate)

Check resume state first:
```bash
python3 -c "
import json, sys
with open('run/run_summary.json') as f: s = json.load(f)
if s.get('lastCompletedStep', 0) >= 11.5:
    print('Step 11b already completed — skipping.'); sys.exit(0)
"
```
If already completed, skip to Step 12.

1. **Generate alert candidates using jobscout_alerts.py:**

```bash
python3 "$SCOUT_DIR/jobscout_alerts.py" \
  --data "$SCOUT_DIR/data.json" \
  --state "$SCOUT_DIR/job-scout-state.json" \
  --applied "$SCOUT_DIR/job-scout-applied.json" \
  --config "$SCOUT_DIR/config/jobscout_config.json" \
  --out "$SCOUT_DIR/run/alert_candidates.json"
```

If this script fails, log the failure and skip Steps 11b(2–4) and Step 12 (Slack alerts). Do not abort the rest of the run.

2. **Spawn reviewer sub-agents (up to 5 in parallel):**

   Read `run/alert_candidates.json`. Load candidates as:
   ```python
   payload = json.load(open("run/alert_candidates.json"))
   candidates = payload["candidates"]  # NOT json.load directly as a list
   ```
   For each candidate where `alertApproved` is `null`:
   - Read `SCOUT_DIR/jobscout_alert_reviewer.md`
   - Replace `{CANDIDATE_JSON}` with the full JSON of this candidate
   - Spawn a sub-agent using the `Agent` tool with the substituted prompt
   - Run up to **5 reviewer agents in parallel**; wait for all to complete before launching the next batch of 5

3. **Write reviewer decisions back to `run/alert_candidates.json`:**

   Each reviewer returns JSON with `alertApproved`, `riskFlags`, and `finalReason`. For each result:
   - Set `alertApproved` to the reviewer's returned value
   - Merge any new `riskFlags` into the existing array (deduplicate — do not add a flag already present)
   - If `alertApproved` is `false`, set `alertRejectionReason` to `finalReason`
   - If `alertApproved` is `true`, leave `alertRejectionReason` as `null`

   After processing all candidates, write the updated wrapper object back to `run/alert_candidates.json`, preserving the envelope:
   ```json
   {"generatedAt": "...", "totalCandidates": N, "candidates": [...updated...]}
   ```

4. **Update run_summary.json:**

```python
python3 -c "
import json

with open('run/alert_candidates.json') as f:
    payload = json.load(f)
candidates = payload['candidates']

approved = sum(1 for c in candidates if c.get('alertApproved') == True)
rejected = sum(1 for c in candidates if c.get('alertApproved') == False)

with open('run/run_summary.json') as f:
    summary = json.load(f)

summary['alertReviewedCount'] = len(candidates)
summary['alertRejectedCount'] = rejected
summary['alertCount'] = approved

with open('run/run_summary.json', 'w') as f:
    json.dump(summary, f, indent=2)

print(f'Alert review complete: {approved} approved, {rejected} rejected')
"
```

```bash
python3 -c "
import json
with open('run/run_summary.json') as f: s = json.load(f)
s['lastCompletedStep'] = 11.5
with open('run/run_summary.json', 'w') as f: json.dump(s, f, indent=2)
"
```

5. Proceed to Step 12 — Slack alerts. Only candidates where `alertApproved == true` will be posted.

---

## Step 12 — Slack alerts

Check resume state first:
```bash
python3 -c "
import json, sys
with open('run/run_summary.json') as f: s = json.load(f)
if s.get('lastCompletedStep', 0) >= 12:
    print('Step 12 already completed — skipping.'); sys.exit(0)
"
```
If already completed, skip to Step 13.

Read `run/alert_candidates.json`. Post to your configured Slack channel (`slackChannel` in config; skip if empty) for jobs where ALL are true:
- `alertApproved == true`
- `_score >= 7`
- `geoFit == true`
- `applied == false`
- `caExcluded == false`
- Remote jobs additionally: salary >= $143K OR salary unknown/contract
- OC jobs: no salary threshold

Format each alert:
```
[Score N] Job Title — Company
Location | Salary
<1-line reason>
<LinkedIn URL>
```

**Slack posting state tracking** — check and update `slackPosted` in `run/run_summary.json` before and after each score tier to make posting idempotent across context compaction:

Before posting score-9 jobs: read `run_summary.json`; if `slackPosted.score9 == true`, skip this tier.
After successfully posting all score-9 jobs: set `slackPosted.score9 = true` and write `run_summary.json`.

Before posting score-8 jobs: read `run_summary.json`; if `slackPosted.score8 == true`, skip this tier.
After successfully posting all score-8 jobs: set `slackPosted.score8 = true` and write `run_summary.json`.

Before posting score-7 jobs: read `run_summary.json`; if `slackPosted.score7 == true`, skip this tier.
After successfully posting all score-7 jobs: set `slackPosted.score7 = true` and write `run_summary.json`.

After all tiers are posted, write `alertedAt` to state so these jobs are never re-alerted:

```bash
python3 "$SCOUT_DIR/jobscout_mark_alerted.py" \
  --candidates "$SCOUT_DIR/run/alert_candidates.json" \
  --state "$SCOUT_DIR/job-scout-state.json"
```

```bash
python3 -c "
import json
with open('run/run_summary.json') as f: s = json.load(f)
s['lastCompletedStep'] = 12
with open('run/run_summary.json', 'w') as f: json.dump(s, f, indent=2)
"
```

---

## Step 13 — Save seen IDs

Call `jobscout_seen.py` — it reads scan files from `run/`, merges new IDs into `job-scout-seen.json` (using the correct `ids` key), enforces retention and the `maxIds` cap, and writes an updated run summary:

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
s['lastCompletedStep'] = 13
with open('run/run_summary.json', 'w') as f: json.dump(s, f, indent=2)
"
```

---

## Step 13b — Archive run summary

```bash
mkdir -p "$SCOUT_DIR/run_summaries"
cp "$SCOUT_DIR/run/run_summary.json" \
  "$SCOUT_DIR/run_summaries/run_summary_$(date +%Y-%m-%d_%H%M%S).json"
echo "Run summary archived."
```

This preserves run history for the weekly report. The `run/` directory is cleared at the start of each run, so this archive is the only persistent record.

---

## Step 14 — Weekly report (Sunday 4am runs only)

Check whether today is Sunday:

```bash
if [ "$(date +%u)" = "7" ]; then
  echo "Sunday run — generating weekly report."
  python3 "$SCOUT_DIR/jobscout_weekly.py" \
    --data "$SCOUT_DIR/data.json" \
    --applied "$SCOUT_DIR/job-scout-applied.json" \
    --state "$SCOUT_DIR/job-scout-state.json" \
    --run-summaries "$SCOUT_DIR/run_summaries" \
    --days 7
else
  echo "Not Sunday — skipping weekly report."
fi
```

If the weekly report script fails, log the failure but continue to git commit.

---

## Step 15 — Git commit

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
print(f'job-scout run {date.today()}: {new_count} new jobs, {alert_count} alerts')
" > /tmp/js-commit-msg.txt

git add \
  jobscout_core.py jobscout_prep.py jobscout_merge.py jobscout_build.py \
  jobscout_alerts.py jobscout_mark_alerted.py jobscout_weekly.py \
  jobscout_ats_scraper.py \
  jobscout_score_agent.md jobscout_alert_reviewer.md \
  job_scout_browser.py \
  commute_map.json config/jobscout_config.json config/target_employers.json README.md \
  data.json job-scout-seen.json job-scout-state.json \
  job-scout-applied.json job-scout-overrides.json \
  job-scout-stars.json job-scout-status.json \
  run/run_summary.json \
  run_summaries/ reports/ 2>/dev/null || true

git -c user.name="Job Scout" -c user.email="scout@local" \
  commit -F /tmp/js-commit-msg.txt || echo "Nothing to commit."

# Sync committed state to your configured git remote (optional).
# Auth is supplied by the macOS Keychain credential helper. A no-op when there
# is nothing new; if a prior run failed to push, this catches up. Failures are
# logged, not retried — the next run will push again.
git push origin HEAD || echo "git push failed (state committed locally; will sync next run)."
```

---

## Failure Handling

Continue after recoverable failures. Do NOT perform state-mutating steps if required upstream artifacts are missing, empty due to failure, or invalid. Record every failure in `run/run_summary.json` under `failedSteps`.

**Step-specific rules:**

| Condition | Action |
|-----------|--------|
| Remote scrape fails, OC succeeds | Continue with OC only; mark run partial |
| OC scrape fails, remote succeeds | Continue with remote only; mark run partial |
| Both scrapes fail | Skip ALL remaining steps; do not update seen IDs, workbook, Slack, or git |
| Metadata scan partially fails | Continue with valid records only |
| Description fetch partially fails | Score available descriptions; log failures |
| LLM scoring chunk fails | Retry once; if still failing, exclude that chunk and record failure |
| Merge fails | Do not run build, Slack, seen-ID update, or git commit |
| Build fails | Do not post Slack, update seen IDs, or commit git |
| Slack posting fails | Continue to seen-ID update and git if prior validation passed |
| Weekly report fails | Log failure; continue to git commit |
| Git commit fails | Log failure; do not retry |
| Git push fails | Log failure; do not retry (next run catches up) |

**Validation gates — check before running each mutating step:**

- Before updating seen IDs: confirm `run/scan_remote.jsonl` or `run/scan_oc.jsonl` exists and is non-empty
- Before rebuilding the workbook: confirm `run/scored.json` exists and contains valid JSON
- Before posting Slack alerts: confirm workbook build succeeded and `run/scored.json` is valid
- Before committing to git: confirm run status is not `failed`

**Final step:** Update `run/run_summary.json` with `finishedAt` (ISO timestamp) and `status` (`success`, `partial`, or `failed`). Use counts from `run_summary.json` in the git commit message instead of the N/M placeholders.
