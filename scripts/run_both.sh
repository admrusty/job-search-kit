#!/usr/bin/env bash
#
# run_both.sh — run Job Scout routines on demand (backs the /scrape command).
#
# The two routines share run.lock and the run/ directory, so they CANNOT run at
# the same time. They run SEQUENTIALLY: wide first (~13 min), then alerts
# (LinkedIn + targeted ATS, ~15 min).
#
# Usage: run_both.sh [wide|alerts|both]   (default: both)

set -uo pipefail

SCOUT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RP="$SCOUT_DIR/scripts/run_pipeline.sh"

which="${1:-both}"
[ -z "$which" ] && which="both"

case "$which" in
  wide)             bash "$RP" wide ;;
  alerts|linkedin)  bash "$RP" alerts ;;
  both)             bash "$RP" wide && bash "$RP" alerts ;;
  *) echo "usage: run_both.sh [wide|alerts|both]" >&2; exit 2 ;;
esac
