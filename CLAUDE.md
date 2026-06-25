# Claude Code instructions — Job Search Kit

This is a single Claude Code workspace that does two things from **one shared candidate profile**:

1. **Find & score jobs** — scrapes public job boards (and optionally LinkedIn), scores each role against the candidate, and shows matches in a local dashboard.
2. **Draft applications** — turns a chosen job into a tailored resume + cover letter, with ATS, voice, and truth-audit checks, rendered to DOCX.

The two halves are connected: from the dashboard, the "⤓ save" button drops a job into `inputs/`; running `/draft` then writes a tailored application to `~/Documents/Resumes/<Company - Role Title>/`.

## Single source of truth

`Context/Master Profile.md` is the verified record of the candidate's facts, roles, dates, accomplishments, tools, certifications, metrics, and claim calibration. It is created once by `/setup` from `Context/Master Profile - TEMPLATE.md`.

**Both halves read from it.** The drafting agents quote it directly; `/setup` also *derives* the job-scoring criteria from it (filling `config/jobscout_config.json`, `jobscout_score_agent.md`, and `jobscout_alert_reviewer.md`). If `Context/Master Profile.md` does not exist yet, the workspace is not set up — tell the user to run `/setup` first.

## First-time setup

The user runs `/setup` once. It builds the Master Profile (easiest: paste an existing resume), derives the job-search config + scoring profile from it, sets the writing voice, runs a first (zero-account) scrape, opens the dashboard, and creates Desktop shortcuts. No accounts are required for the core experience.

## The two halves

### Job finder (Python pipeline + dashboard)
- Engine: `jobscout_*.py` (scrape → prep → score → merge → build → alert) and `job_scout_browser.py` (local dashboard at http://127.0.0.1:8733/).
- Run on demand with `/scrape` (both pipelines) or `bash scripts/run_pipeline.sh wide` (free ATS only, no accounts).
- Scoring runs `claude -p` per chunk via `jobscout_llm_stage.py` — **always sequential (`--concurrency 1`)**; concurrent `claude` instances (including an active interactive session) can stall it. The scheduled 2am/4am runs avoid this.
- Pipeline reference docs: `docs/pipeline-wide.md`, `docs/pipeline-alerts.md`.

### Application drafter (agents + DOCX)
- Use `/draft` as the primary workflow. Agents live in `.claude/agents/`; renderers are `Context/resume_style.js` and `Context/cover_letter_style.js`.
- A normal pass produces NOTES.md, job-analysis.md, resume-content.json, resume-preview.md, cover-letter.md, ats-review.md, voice-style-review.md, truth-audit.md, final-checklist.md, and rendered DOCX.
- Output: `~/Documents/Resumes/<Company - Role Title>/` (override with `RESUME_OUTPUTS_DIR`); supporting files go in the job folder's `Resources/`.

## Version control

This workspace auto-commits its own changes at the end of each turn via the Stop hook in `.claude/settings.json` (`scripts/auto_commit.sh`); it never pushes automatically. Personal data and runtime files are gitignored. Optionally the output directory can be its own git repo, synced with `scripts/sync_outputs.sh "<Company - Role Title>"`.

## Truth policy (applies to all drafting)

- Never invent employers, titles, dates, education, certifications, tools, metrics, projects, responsibilities, outcomes, or compensation.
- Do not add facts from prior memory unless present in `Context/Master Profile.md` or supplied by the user in the current job file.
- If a new fact surfaces, flag it as needing a Master Profile update before using it as a strong claim.
- Do not represent collaboration as sole ownership; do not make AI/technical claims stronger than the Master Profile supports; do not imply hands-on expertise where the source only supports exposure.

## Formatting & review gates (drafting)

- Working drafts in Markdown; final resume DOCX via `Context/resume_style.js` + `resume-content.json`. No text boxes, columns, tables, icons, or graphic layouts for ATS resumes. Follow the em-dash policy in `Context/Resume_DOCX_Style_Spec.md`.
- Before calling a package final, run: ATS review, voice/style review, truth audit, final checklist. Do not mark ready if any high-severity truth or formatting issue remains.
