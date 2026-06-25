---
description: Tailor a resume and cover letter from a supplied job description using the candidate's canonical Instructions, Master Profile, DOCX style rules, ATS review, voice/style review, and truth audit.
argument-hint: path/to/job-description.md
arguments: [job_file]
disable-model-invocation: true
allowed-tools: Read, Write, Edit, Glob, Grep, Bash, Agent
---

# Tailor application workflow

Input job file: `$job_file`

Follow this sequence exactly.

## 0. Resolve job file

If `$job_file` is set and the file exists, proceed to step 1.

If `$job_file` is not set:

- List all files in `inputs/` excluding `.gitkeep`.
- If no files are found: tell the user to add a job description to `inputs/` and stop.
- If exactly one file is found: tell the user which file was found ("Found one job file: `X` — using it.") and proceed automatically.
- If multiple files are found: list them numbered and ask the user which job to work on. Stop and wait for the answer before continuing.

## 1. Read job input

Read `$job_file` only. Subagents handle reading canonical context files.

## 2. Validate job input

Determine whether the job file contains:

- Tracker TSV paste
- Role title
- Company name
- Location/workplace
- Salary/compensation
- URL
- Manual notes
- Full job description

If the job description is missing or incomplete, ask for the missing JD and stop.

If required clarifying questions are triggered by `Context/Instructions.md`, ask the fewest necessary questions and stop unless the user explicitly says to proceed with conservative assumptions.

## 2b. Identify possible Master Profile updates

Scan everything shared in this session — the job file, the user's answers to clarifying questions, any corrections or additions. Identify any candidate facts that are new and not present in `Context/Master Profile.md`.

Do not edit `Context/Master Profile.md` during the `/draft` workflow.

Always write `<job-folder>/Resources/master-profile-update-candidates.md`, even if no new facts were found.

If new facts are found, use this format for each:

```markdown
# Master Profile Update Candidates — <Company - Role Title>

## Candidate update 1

- New fact:
- Source in this session:
- Suggested Master Profile section:
- Suggested wording:
- Risk level: low / medium / high
- Can be used in this application now?: yes, conservatively / no, needs user confirmation
```

If no new facts are found, write:

```markdown
# Master Profile Update Candidates — <Company - Role Title>

No new candidate facts identified in this application session.
```

## 3. Create output folders

Extract the company name and role title from the job file. Format the folder name as:

`Company - Role Title`

Use the exact company name and role title as they appear in the job description — preserve capitalization, punctuation, and abbreviations (e.g., `iHerb - Sr. Manager Localization Content`). Do not slugify or lowercase.

**Check for existing folder first.** Before creating the job folder, check whether `~/Documents/Resumes/Company - Role Title/` already exists.

If it already exists:
- List the folder contents briefly (file names only, one line each).
- Ask the user: "A folder already exists at this path. How would you like to proceed?
  1. **Overwrite** — continue and replace files as they are written (existing files not touched by this run are preserved).
  2. **Version** — create a new folder named `<Company - Role Title> (2)` (or the next available number) instead.
  3. **Stop** — I will rename or delete the existing folder manually before we continue."
- Wait for the user's answer before proceeding. Do not create any files until they respond.

If the folder does not exist, create it:

- **Job folder:** `~/Documents/Resumes/Company - Role Title/`
- **Resources subfolder:** `~/Documents/Resumes/Company - Role Title/Resources/`

The DOCX files go in the job folder. All other files (NOTES.md, TODO.md, job-analysis.md, resume-preview.md, resume-content.json, cover-letter.md, ats-review.md, voice-style-review.md, truth-audit.md, final-checklist.md) go in Resources/.

Do not use the local `outputs/` folder.

## 3b. Preserve job description

As soon as the job folder and Resources/ subfolder exist, copy the full job description text to:

`<job-folder>/Resources/job-description-source.md`

If the job description came from a TSV paste, write the `description` field verbatim. If it came from a file, write the file's full contents. This preserves the original posting in case the URL goes offline.

## 4. Research the company

Run `company-researcher` first.

- Reads the job file.
- Writes `<job-folder>/Resources/NOTES.md`.
- Writes `<job-folder>/Resources/TODO.md`.
- Records public-source findings and research gaps.

Do not start job analysis until `Resources/NOTES.md` exists, even if the notes say web research was unavailable.

## 5. Analyze the job

Run `job-analyzer` after company research completes.

- Reads the job file.
- Reads `Context/Master Profile.md`.
- Reads `<job-folder>/Resources/NOTES.md`.
- Writes `<job-folder>/Resources/job-analysis.md`.

## 6. Draft the resume and cover letter (run in parallel)

Run both subagents at the same time:

- `resume-tailor` — reads `Context/Master Profile.md`, `Context/Resume_DOCX_Style_Spec.md`, the job file, `Resources/job-analysis.md`, and `Resources/NOTES.md`. Writes `Resources/resume-preview.md`, `Resources/resume-content.json`, and `Resources/resume-evidence-map.md`.
- `cover-letter-drafter` — reads `Context/Master Profile.md`, the job file, `Resources/job-analysis.md`, and `Resources/NOTES.md`. Writes `Resources/cover-letter.md` and `Resources/cover-letter-content.json`.

`Resources/resume-content.json` must match the schema described in `Context/resume_style.js`.

## 7. Run all review gates (run in parallel)

Run all three subagents at the same time:

- `ats-reviewer` — reads the resume, JSON, cover letter, job file, job analysis, and style spec. Writes `Resources/ats-review.md`.
- `voice-style-reviewer` — reads the resume and cover letter. Writes `Resources/voice-style-review.md`.
- `truth-auditor` — reads the resume, cover letter, Master Profile, job file, NOTES, and evidence map. Writes `Resources/truth-audit.md`.

## 8. Revise once if needed

If any review contains a High issue, revise the affected file once before rendering.

If only Medium or Low issues remain, make a targeted judgment call. Fix Medium issues if practical without introducing new risk. Do not churn on Low issues.

After one revision pass, add `Post-revision status` to each review report.

If any High issue remains after the single revision pass, stop. Do not render DOCX files. Report the unresolved High issue in `Resources/final-checklist.md`.

**After any revision to the cover letter, run this deterministic check before proceeding:**

```bash
grep -n "—" "<job-folder>/Resources/cover-letter.md"
```

If any em dashes appear in body paragraph lines (not in structural lines like the header or date), fix them immediately — rewrite with a colon or restructure the sentence. Then re-run the grep to confirm zero hits before moving on. Do not rely on the voice reviewer's count; always verify with grep.

## 9. Strategic evaluation

Run `application-evaluator` after the review gates pass (or after the revision pass if one was needed).

The evaluator reads:

- `Context/Master Profile.md`
- `Resources/job-analysis.md`
- `Resources/NOTES.md`
- `Resources/resume-preview.md`
- `Resources/cover-letter.md`
- `Resources/ats-review.md`, `Resources/voice-style-review.md`, `Resources/truth-audit.md` (for context)

It writes `Resources/evaluation-report.md`.

If the evaluator's verdict is "revise materially" or "deprioritize," stop before rendering. Report the verdict and the top issues in `Resources/final-checklist.md` and ask the user how to proceed.

If the verdict is "submit," "submit after minor fixes," or similar, continue to rendering. Apply any "must fix before submitting" edits first if they are quick and unambiguous.

## 10. Check review gate statuses and validate JSON

Before rendering, perform two checks in order:

**Check 1 — Review gate machine-readable statuses:**

Read `Resources/truth-audit.md` and look for a line beginning with `TRUTH_AUDIT_STATUS:`.
- If the line reads `TRUTH_AUDIT_STATUS: FAIL`, stop immediately. Do not render. Report the unresolved issue in `Resources/final-checklist.md`.

Read `Resources/ats-review.md` for an equivalent `ATS_REVIEW_STATUS:` line.
- If it reads `ATS_REVIEW_STATUS: FAIL`, stop immediately.

Read `Resources/voice-style-review.md` for `VOICE_REVIEW_STATUS:`.
- If it reads `VOICE_REVIEW_STATUS: FAIL`, stop immediately (only if a High issue was identified — Medium-only fails should not block).

**Check 2 — JSON validation:**

Run:

```bash
python3 scripts/validate_application.py "<job-folder>"
```

If validation fails, stop and fix the JSON. Do not render until both checks pass.

## 11. Render DOCX files

Infer the company name from the job file or `Resources/job-analysis.md`.

Run both renderers, writing the DOCX files to the job folder (not Resources/):

```bash
node Context/resume_style.js "<job-folder>/Resources/resume-content.json" "<job-folder>/<Candidate Name> - Resume - <Company>.docx"
node Context/cover_letter_style.js "<job-folder>/Resources/cover-letter-content.json" "<job-folder>/<Candidate Name> - Cover Letter - <Company>.docx"
```

**After each render, verify the output:**

- Check that the DOCX file exists at the reported path.
- Check that the file size is greater than 1 KB (i.e., not empty or corrupted).

If either check fails, stop. Do not mark render status as complete. Report the failure in `Resources/final-checklist.md` with the exact error.

## 12. Final checklist

Write `Resources/final-checklist.md` with:

# Final Checklist — <Company - Role Title>

## Created files

- Resume DOCX: `<job-folder>/<Candidate Name> - Resume - <Company>.docx`
- Cover letter DOCX: `<job-folder>/<Candidate Name> - Cover Letter - <Company>.docx`
- Resources folder: `<job-folder>/Resources/`

## Resource files

- `NOTES.md`
- `TODO.md`
- `job-description-source.md`
- `master-profile-update-candidates.md`
- `job-analysis.md`
- `resume-preview.md`
- `resume-content.json`
- `resume-evidence-map.md`
- `cover-letter.md`
- `cover-letter-content.json`
- `ats-review.md`
- `voice-style-review.md`
- `truth-audit.md`

## Render status

- Resume DOCX rendered: yes / no
- Resume DOCX file size: [size in KB]
- Cover letter DOCX rendered: yes / no
- Cover letter DOCX file size: [size in KB]
- JSON validation passed: yes / no

## Company research status

- Research complete: yes / incomplete (web tools unavailable)
- If incomplete: [describe gaps that need manual research before the cover letter is finalized]

## Review gate status

- ATS review: pass / pass with medium issues / fail
- Voice/style review: pass / pass with medium issues / fail
- Truth audit: pass / pass with medium issues / fail

## Remaining risks

List remaining Medium or Low issues. If any High issue remains, state that the package is not ready.

## Questions for the user

List only decisions the user must make before applying.

## Submission note

This workflow stops at DOCX. Review the DOCX files before applying. Create PDFs manually only after approving the DOCX versions.

Do not mark the package final if any high-severity truth or ATS issue remains.

## 13. Sync to git (optional)

If the output directory (`~/Documents/Resumes`) is a git repository with a remote, sync the completed package after the final checklist is written:

```bash
bash scripts/sync_outputs.sh "<Company - Role Title>"
```

This commits and pushes only the job folder. The script is idempotent — re-running it with no changes is a safe no-op. If the output directory is not a git repo, skip this step.

**Do not sync if any High-severity truth or ATS issue remains.** Fix the issue first, then run the sync. (Medium/Low issues noted in the final checklist are fine to sync.)
