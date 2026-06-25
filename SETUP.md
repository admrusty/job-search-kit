# Job Search Kit — Setup

One **Claude Code** workspace that finds & scores jobs for you *and* drafts tailored resumes and cover letters — all from a single profile you set up once. Optional add-ons: LinkedIn (Apify), Slack alerts, GitHub backup, Gmail applied-sync.

## One step

Open this folder in **Claude Code** and type:

```
/setup
```

Claude does everything: it builds your profile (easiest — paste your existing resume), uses it to find your first batch of jobs, opens your dashboard, and puts two shortcuts on your Desktop. **You don't edit any files.**

## What you need

- **Required:** a Mac, **Claude Code**, **Python 3**, and **Node.js** (for rendering Word docs — `/setup` checks for it). The core (job finding + scoring + dashboard + resume drafting) needs **no other accounts**.
- **Optional (add later, `/setup` guides you):**
  - **Apify** (free tier) — adds LinkedIn jobs
  - **Slack** — new strong matches as alerts
  - **GitHub** — automatic backup of your data
  - **Gmail** — auto-track which jobs you've applied to
  - **Scheduling** — auto-scrape overnight (2am/4am)

## What you get

- **My Resumes** shortcut on your Desktop → finished applications in `~/Documents/Resumes/`.
- **Start Job Dashboard** shortcut on your Desktop → opens your local dashboard anytime.

## After setup

- Fresh jobs on demand: **`/scrape`**
- Draft an application: click a job's **⤓ save** in the dashboard, then run **`/draft`**
- Change criteria or add an integration: re-run **`/setup`** (or just ask Claude)

Not technical? You don't need to be — Claude Code does the work. Just run `/setup` and answer the questions.
