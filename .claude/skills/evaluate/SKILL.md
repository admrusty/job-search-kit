---
description: Strategically evaluate an existing resume and cover letter against a job posting. Runs after drafting and review gates, before DOCX rendering.
argument-hint: ~/Documents/Resumes/Company - Role Title/
arguments: [application_folder]
disable-model-invocation: true
allowed-tools: Read, Write, Glob, Agent
---

# Evaluate application workflow

Application folder: `$application_folder`

## 0. Resolve application folder

If `$application_folder` is set and the folder exists, proceed to step 1.

If `$application_folder` is not set:

- List all subfolders in `~/Documents/Resumes/` (one level deep, no recursion).
- If no folders are found: tell the user no application folders were found and stop.
- If exactly one folder is found: tell the user which folder was found and proceed automatically.
- If multiple folders are found: list them numbered and ask the user which application to evaluate. Stop and wait for the answer before continuing.

## 1. Check for required draft files

Look for:

- `$application_folder/Resources/resume-preview.md`
- `$application_folder/Resources/cover-letter.md`
- `$application_folder/Resources/job-analysis.md`

If `job-analysis.md` is missing, stop and tell the user to run `/draft` first.

If `resume-preview.md` or `cover-letter.md` are missing:

Ask the user:

> I couldn't find the draft resume and/or cover letter in this folder. Please paste the resume content and cover letter content directly into the chat and I will use them for evaluation. You can paste one at a time.

Wait for the user to paste both. Do not proceed until both are available either from files or from the chat.

## 2. Read supporting context

Read:

- `$application_folder/Resources/job-analysis.md`
- `$application_folder/Resources/NOTES.md` (if it exists)
- `$application_folder/Resources/ats-review.md` (if it exists — read for context, do not re-run)
- `$application_folder/Resources/voice-style-review.md` (if it exists — read for context, do not re-run)
- `$application_folder/Resources/truth-audit.md` (if it exists — read for context, do not re-run)

## 3. Run the evaluator

Spawn the `application-evaluator` agent with:

- The resolved resume content (from file or pasted)
- The resolved cover letter content (from file or pasted)
- The path to `job-analysis.md`
- The path to `NOTES.md` (if present)
- The paths to any existing review reports (if present)
- The output path: `$application_folder/Resources/evaluation-report.md`

## 4. Done

Tell the user:

> Evaluation complete. Report written to `$application_folder/Resources/evaluation-report.md`.

If the evaluator's verdict is "submit after fixes" or stronger, note the top fixes inline. If the verdict is "revise materially" or weaker, summarize the biggest issue in one sentence.
