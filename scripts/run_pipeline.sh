#!/usr/bin/env bash
#
# run_pipeline.sh — headless driver for the Job Scout routines.
# This is the launchd entrypoint that replaces the desktop-app (Cowork) routines.
# It runs the canonical skill file through a non-interactive `claude -p`.
#
# Usage:
#   scripts/run_pipeline.sh alerts   # SKILL.md            (LinkedIn via Apify; 4am & 3pm)
#   scripts/run_pipeline.sh wide     # SKILL-wide-scraper.md (ATS HTTP scrape; 2am)
#
# Concurrency is handled by the skill's own run.lock (age-check + rename in Step 0).

set -uo pipefail

ROUTINE="${1:-}"
SCOUT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

case "$ROUTINE" in
  wide)
    # Migrated to the deterministic orchestrator (shell runs the pipeline; claude -p
    # only for scoring/review/Slack). Robust for unattended headless execution.
    exec /bin/bash "$SCOUT_DIR/scripts/run_wide_pipeline.sh"
    ;;
  alerts)
    # Deterministic orchestrator: LinkedIn (Apify REST) + ATS scrape, then claude -p
    # only for scoring/review/Slack/Gmail. Robust for unattended headless execution.
    exec /bin/bash "$SCOUT_DIR/scripts/run_alerts_pipeline.sh"
    ;;
  *) echo "usage: run_pipeline.sh alerts|wide" >&2; exit 2 ;;
esac

# launchd starts jobs with a minimal PATH; set a full one so python3/node/git/claude resolve.
export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin:${PATH:-}"

cd "$SCOUT_DIR" || { echo "SCOUT_DIR missing: $SCOUT_DIR" >&2; exit 1; }
mkdir -p logs

CLAUDE_BIN="$(command -v claude || true)"
if [ -z "$CLAUDE_BIN" ]; then
  echo "claude CLI not found on PATH ($PATH)" >&2
  exit 1
fi

LOG="logs/${ROUTINE}-$(date +%Y-%m-%d_%H%M%S).log"

PROMPT="You are running unattended as a scheduled job (no human is watching). Read the file ${SKILL} in ${SCOUT_DIR} in full, then execute the entire ${LABEL} pipeline it describes, end to end, following every step in order. Do not ask any questions. Continue past recoverable failures exactly as the skill's Failure Handling section instructs. When finished, print a single-line summary of the run (new jobs, alerts, status)."

{
  echo "=== ${LABEL} run started $(date '+%Y-%m-%d %H:%M:%S %Z') ==="
  echo "--- skill: ${SKILL} | claude: ${CLAUDE_BIN} ---"
  "$CLAUDE_BIN" -p "$PROMPT" --dangerously-skip-permissions
  rc=$?
  echo "=== ${LABEL} run finished $(date '+%Y-%m-%d %H:%M:%S %Z') (exit ${rc}) ==="
} >> "$LOG" 2>&1

# Surface the dated log path and exit code to whatever invoked us (launchd captures this too).
echo "log: $SCOUT_DIR/$LOG"
exit "${rc:-0}"
