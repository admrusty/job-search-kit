from pathlib import Path

required = [
    "Context/Instructions.md",
    "Context/Master Profile.md",
    "Context/Resume_DOCX_Style_Spec.md",
    "Context/resume_style.js",
    "Context/cover_letter_style.js",
    "CLAUDE.md",
    ".claude/skills/draft/SKILL.md",
    ".claude/skills/render-resume-docx/SKILL.md",
    ".claude/skills/review-application/SKILL.md",
    ".claude/agents/company-researcher.md",
    ".claude/agents/job-analyzer.md",
    ".claude/agents/resume-tailor.md",
    ".claude/agents/cover-letter-drafter.md",
    ".claude/agents/ats-reviewer.md",
    ".claude/agents/voice-style-reviewer.md",
    ".claude/agents/truth-auditor.md",
    "inputs/.gitkeep",
]

forbidden = [
    ".claude/.claude",
    "outputs",
    "__MACOSX",
]

missing = [p for p in required if not Path(p).exists()]
present_forbidden = [p for p in forbidden if Path(p).exists()]

if missing:
    print("Missing required files:")
    for p in missing:
        print(f"- {p}")

if present_forbidden:
    print("Forbidden generated or stale paths are present:")
    for p in present_forbidden:
        print(f"- {p}")

if missing or present_forbidden:
    raise SystemExit(1)

print("Context check passed. Required files are present and stale paths are absent.")
