---
description: Start the job scrapers on demand (wide + LinkedIn/alerts), detached
argument-hint: "[wide|alerts|both]  — default: both"
allowed-tools: Bash(nohup bash:*)
---

Start the job scraper routine(s) on demand. Use the argument `$ARGUMENTS` to pick which (`wide`, `alerts`, or `both`); if no argument was given, use `both`.

Launch them **detached and then STOP**. Do NOT monitor, tail logs, or poll the run — this session must stay idle while the scrapers run, because their headless `claude -p` scoring/review steps deadlock if another Claude instance is making model calls at the same time.

Run exactly this one command (replace `both` with the user's argument if they gave one), then stop:

```bash
nohup bash "$CLAUDE_PROJECT_DIR/scripts/run_both.sh" both >/dev/null 2>&1 & echo "started scraper(s) [both] — pid $!"
```

Then report to the user in a few lines and end your turn:
- What launched and that it's running **detached in the background**. If `both`, note they run **sequentially** — wide first (~13 min), then alerts/LinkedIn (~15 min), ~30 min total — because they share a run lock and can't overlap.
- ⚠️ **Leave Claude idle until it finishes.** Don't start other work in this or any Claude session during the run, or the scrapers' scoring step will stall (concurrent `claude` instances deadlock). Open/idle is fine; actively working is not.
- Progress (optional, for them to run): `tail -f "$CLAUDE_PROJECT_DIR"/logs/wide-*.log` then `logs/alerts-*.log`.
- When done, new matches appear in the dashboard; if Slack/GitHub are configured, qualifying alerts post and results commit — same as the scheduled runs.

Do NOT make any further tool calls after the launch command. Do not check status or tail logs yourself.
