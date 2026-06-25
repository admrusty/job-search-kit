---
description: Render a DOCX resume from an application folder's resume-content.json using Context/resume_style.js.
argument-hint: ~/Documents/Resumes/Company - Role Title/
arguments: [application_folder]
disable-model-invocation: true
allowed-tools: Read, Write, Glob, Bash
---

# Render resume DOCX

Application folder: `$application_folder`

1. Read `Context/Resume_DOCX_Style_Spec.md`.
2. Confirm `$application_folder/Resources/resume-content.json` exists.
3. Confirm `Context/resume_style.js` exists.
4. Run JSON validation:

```bash
python3 scripts/validate_application.py "$application_folder"
```

If validation fails, stop and fix the JSON. Do not render until validation passes.

5. Infer the company name from the folder, job file, or job analysis.
6. Run:

```bash
node Context/resume_style.js "$application_folder/Resources/resume-content.json" "$application_folder/<Candidate Name> - Resume - <Company>.docx"
```

7. Verify the output:
   - Confirm the DOCX file exists at the reported path.
   - Confirm the file size is greater than 1 KB.
   - If either check fails, report the failure and stop — do not mark the render as complete.

8. Report the created DOCX path and file size.

Do not edit `Context/resume_style.js` unless the user explicitly asks.
