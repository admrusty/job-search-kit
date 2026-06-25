"""jobscout_mark_alerted.py — Write alertedAt to job-scout-state.json.

Called after Slack alerts are posted to permanently suppress re-alerting.
jobscout_alerts.py skips any job where state.jobs[id].alertedAt is set.

Usage:
    python3 jobscout_mark_alerted.py \
        --candidates run/alert_candidates.json \
        --state job-scout-state.json

Marks every candidate where alertApproved == true.
"""
import argparse
import json
import os
import sys
from datetime import datetime, timezone

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--candidates", default=os.path.join(SCRIPT_DIR, "run", "alert_candidates.json"))
    ap.add_argument("--state",      default=os.path.join(SCRIPT_DIR, "job-scout-state.json"))
    args = ap.parse_args()

    with open(args.candidates) as f:
        payload = json.load(f)
    candidates = payload.get("candidates", [])

    with open(args.state) as f:
        state = json.load(f)
    if "jobs" not in state:
        state["jobs"] = {}

    now_iso = datetime.now(timezone.utc).isoformat()
    marked = 0
    for c in candidates:
        if c.get("alertApproved") is True:
            job_id = str(c.get("id", ""))
            if not job_id:
                continue
            if job_id not in state["jobs"]:
                state["jobs"][job_id] = {}
            state["jobs"][job_id]["alertedAt"] = now_iso
            marked += 1

    with open(args.state, "w") as f:
        json.dump(state, f, indent=2)

    print(f"alertedAt written for {marked} jobs → {args.state}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
