---
description: Review an existing application output folder for ATS hygiene, voice/style, truthfulness, and DOCX formatting readiness.
argument-hint: ~/Documents/Resumes/Company - Role Title/
arguments: [application_folder]
disable-model-invocation: true
allowed-tools: Read, Write, Edit, Glob, Grep, Bash, Agent
---

# Review application workflow

Application folder: `$application_folder`

Read:

- `CLAUDE.md`
- `Context/Instructions.md`
- `Context/Master Profile.md`
- `Context/Resume_DOCX_Style_Spec.md`
- All Markdown and JSON files in `$application_folder`

Run:

1. `ats-reviewer`
2. `voice-style-reviewer`
3. `truth-auditor`

Write or update:

- `$application_folder/Resources/ats-review.md`
- `$application_folder/Resources/voice-style-review.md`
- `$application_folder/Resources/truth-audit.md`
- `$application_folder/Resources/final-checklist.md`

Each reviewer uses the High/Medium/Low severity rubric. The final-checklist.md must include:

- Overall status: ready / not ready
- ATS review: pass / pass with medium issues / fail
- Voice/style review: pass / pass with medium issues / fail
- Truth audit: pass / pass with medium issues / fail
- List of any remaining High issues (if any remain, the package is not ready)
- List of remaining Medium and Low issues
- Questions for the user

Do not rewrite the application unless the user explicitly asks.
