---
name: setup
description: First-run setup for the Job Search Kit — build the user's Master Profile (from their resume or an interview), DERIVE their job-search config + scoring profile + writing voice from it, run a first zero-account scrape, open the dashboard, and create Desktop shortcuts. Use when the user runs /setup or asks to set up / configure / personalize the kit.
---

You are setting up the **Job Search Kit** for a new, possibly non-technical user. Do ALL the technical work yourself — the user just answers questions in plain language. Be warm, brief, and concrete. **Never make them edit files.**

This one workspace does two things from **one profile**: (1) finds & scores jobs into a local dashboard, and (2) drafts tailored resumes + cover letters. The single source of truth is `Context/Master Profile.md`. Everything else is derived from it.

`SCOUT_DIR` = this project's root (where `job_scout_browser.py` and the `jobscout_*.py` files live). Resolve it once at the start: `SCOUT_DIR="$(pwd)"` from the project root, and use the absolute path when writing Desktop shortcuts and launchd plists.

## Step 0 — Prerequisites (silent)
- `python3 --version` and `uname` = Darwin (macOS). If python3 is missing, tell them to install it from python.org and stop.
- `node --version` + `npm --version` (needed to render Word docs). If present, run `npm install` in `SCOUT_DIR` (installs `docx`). If node is missing, tell them to install Node.js from nodejs.org, then continue — job finding still works without it; only DOCX rendering needs it.
- No accounts are needed for the core path.

## Step 1 — Build the Master Profile (the heart)
This is the single source of truth — accuracy matters; the truth-auditor checks every resume claim against it. Offer the **easy path first**:

> "Do you have an existing resume? Paste its text here (or drop the file in this chat / into `inputs/`) and I'll build your profile from it. Or we can build it together by talking through your background."

- **If they share a resume:** read it and fill `Context/Master Profile - TEMPLATE.md` → save as `Context/Master Profile.md`: contact/identity, professional summary + positioning (target role families, seniority, 3–5 positioning anchors), full work history (company, title, dates, accomplishments with metrics), education, certifications, skills/tools, and the claim-calibration section. Then **read it back in plain language** and ask them to confirm/correct facts — especially dates, titles, and metrics.
- **If they prefer an interview:** walk role-by-role and fill the same template.
- **Claim calibration:** ask which accomplishments they *owned* vs. *contributed to*, and which metrics are *exact* vs. *approximate*; record it so drafts never overclaim.

Save as `Context/Master Profile.md` (NOT the TEMPLATE).

## Step 2 — Derive everything else from the profile (don't re-interview)
Use the profile you just built. Only ask the few things it can't tell you.

**a) Voice (one question):** "How do you want your resumes/cover letters to read?" (e.g. direct & analytical, warm & narrative, concise & metric-driven). Replace `{{VOICE_PROFILE}}` and `{{CANDIDATE_NAME}}` in the four drafting agents: `.claude/agents/{cover-letter-drafter,resume-tailor,voice-style-reviewer,application-evaluator}.md`.

**b) Job-scoring profile (derived):** fill the placeholders in `jobscout_score_agent.md` and `jobscout_alert_reviewer.md` from the Master Profile:
- `{{CANDIDATE_NAME}}`, `{{CANDIDATE_SUMMARY}}` ← identity + professional summary.
- `{{TARGET_LANES}}` ← the profile's target role families (the kinds of roles that fit).
- `{{CANDIDATE_TOOLS}}`, `{{CANDIDATE_SKILLS}}` ← skills/tools section.
- `{{HOME_CITY}}` ← their city/metro; `{{SALARY_FLOOR}}` ← comp floor (ask if not in profile).
- `{{PLACEHOLDERS}}` (deprioritized lanes) ← **ask the one scraper-specific question:** "Any kinds of roles you want to rule OUT (e.g. sales, pure people-management)?"

**c) Search config:** fill `config/jobscout_config.json`:
- `search.remote.keywords` and `search.orangeCounty.keywords` (use the SAME set; "orangeCounty" is just the legacy key for "your local in-person search") — derive 5–15 keyword phrases from the profile's target role families/titles. Build the LinkedIn keyword string as `"phrase one" OR "phrase two" OR …`.
- `search.*.location` + `distance` ← their metro; ask Remote / Hybrid / On-site / any (set `f_WT` accordingly).
- `compensation` floors ← profile §6/comp (or the salary floor you asked).
- `geo.homeCity` ← their city; `targetLanes` / `deprioritizedRoles` ← same as the scoring profile.
- Leave `slackChannel: ""` and `gmailSync: false`.

Show a short plain-English summary of what you configured and let them correct anything.

## Step 3 — First scrape + dashboard (zero accounts) — the payoff
Run the free ATS scrape + scoring so they see real results immediately:
```bash
cd "$SCOUT_DIR" && bash scripts/run_pipeline.sh wide
```
This scrapes public Greenhouse/Lever/Ashby boards (no accounts), scores matches with this Claude Code, and updates the local data. It runs for several minutes and uses `claude -p` internally — tell them to let it finish and to avoid starting other heavy Claude work meanwhile (concurrent Claude sessions can stall it). Then open the dashboard:
```bash
cd "$SCOUT_DIR" && python3 job_scout_browser.py
```
Give them the local URL it prints (http://127.0.0.1:8733/). This is their dashboard.

## Step 4 — Show the resume handoff
Create the output folder up front so it always exists:
```bash
mkdir -p "$HOME/Documents/Resumes"
```
Explain the flow: in the dashboard, a job's **⤓ save** button drops it into this project's `inputs/` folder; then running **`/draft`** writes a tailored resume + cover letter (with ATS/voice/truth checks) to `~/Documents/Resumes/<Company - Role Title>/`. Offer to draft one now if they have a posting.

## Step 5 — Desktop shortcuts (Mac convenience; default yes)
So they never hunt for anything. Tell them you'll add two Desktop shortcuts.

**a) "My Resumes" → the Resumes folder.** Make a Finder alias (fallback to a symlink):
```bash
osascript -e 'tell application "Finder" to make alias file to POSIX file "'"$HOME"'/Documents/Resumes" at desktop' 2>/dev/null \
  && mv -f "$HOME/Desktop/Resumes" "$HOME/Desktop/My Resumes" 2>/dev/null \
  || ln -sfn "$HOME/Documents/Resumes" "$HOME/Desktop/My Resumes"
```

**b) "Start Job Dashboard" → opens the dashboard.** Write a launcher with the REAL project path baked in, then make it executable:
```bash
cat > "$HOME/Desktop/Start Job Dashboard.command" <<EOF
#!/bin/bash
exec "$SCOUT_DIR/Start Dashboard (background).command"
EOF
chmod +x "$HOME/Desktop/Start Job Dashboard.command"
```
Tell them: double-click **Start Job Dashboard** to open the dashboard. A small Terminal window appears — that's the dashboard's engine; keep it open while using the dashboard, close it (or double-click `Stop Dashboard.command` in the project folder) to stop.

## Step 6 — Optional add-ons (offer one at a time; skippable)
Only after the core works:
- **Scheduling** — auto-scrape overnight. Substitute `__SCOUT_DIR__` → the real `$SCOUT_DIR` in `scripts/launchd/com.jobscout.wide.plist` (and `com.jobscout.alerts.plist` if LinkedIn is enabled), write the filled copies to `~/Library/LaunchAgents/`, load with `launchctl bootstrap gui/$(id -u) <plist>`. Default 2am wide / 4am alerts; the Mac must be awake and no active Claude session at run time.
- **LinkedIn (Apify)** — free account → token from console.apify.com/settings/integrations. Save it (read via pbpaste; never print it): `mkdir -p ~/.config/job-scout && pbpaste > ~/.config/job-scout/apify_token && chmod 600 ~/.config/job-scout/apify_token`. Validate: `curl -s "https://api.apify.com/v2/users/me?token=$(cat ~/.config/job-scout/apify_token)"`. Then `run_pipeline.sh alerts` includes LinkedIn.
- **Slack alerts** — pick/create a channel; set `slackChannel` (its channel ID) in config; needs the Slack connector (add via /mcp).
- **GitHub backup** — create a private repo; `git init` + add remote in `$SCOUT_DIR`; runs commit + push automatically.
- **Gmail applied-sync** — set `gmailSync: true`; needs the Gmail connector.

## Finish
Summarize in a few lines: their Master Profile is built and is the single source of truth; job criteria + voice are derived from it; the dashboard is at http://127.0.0.1:8733/ (Desktop "Start Job Dashboard"); finished applications go to ~/Documents/Resumes (Desktop "My Resumes"). Tell them the commands they'll use most: **`/scrape`** (fresh jobs) and **`/draft`** (after ⤓-saving a job). They can re-run `/setup` or just ask you to change anything.
