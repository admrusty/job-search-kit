# Job Search Kit

One Claude Code workspace for your whole job search — it **finds and scores jobs** for you, shows them in a local dashboard, and **drafts a tailored resume + cover letter** for any role you pick. All driven by a single profile you set up once.

You don't have to be technical. Open this folder in **Claude Code**, run **`/setup`**, and answer the questions.

## Quick start

1. Install **Claude Code** for Mac and sign in.
2. In Claude Code, paste:
   `Clone https://github.com/admrusty/job-search-kit into ~/Documents/Job Search Kit and run /setup`
3. Answer the questions (the easiest start: paste your existing resume). `/setup` builds your profile, finds a first batch of jobs, opens your dashboard, and puts two shortcuts on your Desktop:
   - **My Resumes** — your finished applications.
   - **Start Job Dashboard** — double-click to open your dashboard anytime.

That's it. No accounts required to start.

## What it does

**Find & score jobs** → scrapes public job boards, scores each role against *you*, and lists the matches in a local dashboard (stars, dismissals, notes).

**Draft applications** → in the dashboard, click a job's **⤓ save** button to send it to `inputs/`, then run **`/draft`**. You get a tailored resume + cover letter — checked for ATS hygiene, voice, and truthfulness — saved to `~/Documents/Resumes/<Company - Role>/`.

**One profile drives both.** `/setup` builds `Context/Master Profile.md` (your verified background) and derives your job-search criteria from it — so you answer the background questions once.

## Everyday commands

- **`/scrape`** — fetch fresh jobs on demand.
- **`/draft`** — write an application for the job currently in `inputs/`.
- **Start Job Dashboard** (Desktop) — open the dashboard (or run `python3 job_scout_browser.py`).
- Re-run **`/setup`** anytime to change your criteria or add an integration.

## Runs with zero accounts

The core needs no external accounts: free ATS scraping (Greenhouse/Lever/Ashby), scoring via Claude Code, a local dashboard, and the full resume/cover-letter drafter. These are **optional add-ons** `/setup` can wire up later — each is detected and skipped cleanly when absent:

| Add-on | Enables | Gate |
|--------|---------|------|
| **Apify** | LinkedIn jobs | `~/.config/job-scout/apify_token` |
| **Slack** | Alerts posted to a channel | `slackChannel` set in config |
| **Gmail** | Auto-track applied status | `"gmailSync": true` in config |
| **GitHub** | Cloud backup of your data | a git `origin` remote exists |
| **Scheduling** | Auto-scrape at 2am/4am | macOS launchd (`/setup` installs it) |

## Under the hood

- **Job finder:** `jobscout_*.py` (scrape → score → merge → build → alert) + `job_scout_browser.py` (dashboard). Deep reference: [`docs/pipeline-wide.md`](docs/pipeline-wide.md) and [`docs/pipeline-alerts.md`](docs/pipeline-alerts.md). Scoring runs `claude -p` sequentially (`--concurrency 1`) to avoid contention.
- **Application drafter:** agents in `.claude/agents/` + DOCX renderers in `Context/` (`resume_style.js`, `cover_letter_style.js`). Output dir overridable with `RESUME_OUTPUTS_DIR`.
- **Config:** `config/jobscout_config.json` and `Context/Master Profile.md` — both written by `/setup`; editable by hand if you like.

## Tests

```bash
python3 -m pytest tests/ -q
```

Covers salary parsing, geo eligibility, description normalization, and alert selection.

## Known limitations

- Scheduled runs only fire when the Mac is awake and you're logged in.
- Apify (optional) is pay-per-result; a freshness filter keeps runs cheap.
- Scoring and drafting both call `claude -p`; avoid kicking off other heavy Claude work mid-run.
