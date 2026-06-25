#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
from pathlib import Path


# ── Resume schema ──────────────────────────────────────────────────────────────
# Top-level keys required by Context/resume_style.js
REQUIRED_RESUME_KEYS = {"name", "contact", "headline", "sections"}

# Each section must have these
REQUIRED_SECTION_KEYS = {"type", "heading"}

# Within an experience section, each company must have these
REQUIRED_COMPANY_KEYS = {"name", "roles"}

# Within a company, each role must have these
REQUIRED_ROLE_KEYS = {"title", "dates", "bullets"}

# ── Cover letter schema ────────────────────────────────────────────────────────
REQUIRED_COVER_KEYS = {
    "name",
    "contact",
    "date",
    "salutation",
    "paragraphs",
    "valediction",
    "signature",
}


def fail(message: str) -> None:
    print(f"ERROR: {message}")
    raise SystemExit(1)


def load_json(path: Path) -> dict:
    if not path.exists():
        fail(f"Missing file: {path}")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        fail(f"Invalid JSON in {path}: {exc}")


def validate_contact(contact: str, path: Path) -> None:
    """Contact field must contain at least 3 pipe separators: City | email | phone | LinkedIn."""
    if contact.count("|") < 3:
        fail(
            f"{path} contact field must contain at least 3 '|' separators "
            f"(expected: City, ST  |  email  |  phone  |  LinkedIn URL). Got: '{contact}'"
        )


def validate_resume(data: dict, path: Path) -> None:
    # Top-level keys
    missing = REQUIRED_RESUME_KEYS - set(data.keys())
    if missing:
        fail(f"{path} missing required top-level keys: {sorted(missing)}")

    # Headline
    headline = data.get("headline", {})
    if not isinstance(headline, dict) or not headline.get("bold") or not headline.get("rest"):
        fail(f"{path} headline must be an object with non-empty 'bold' and 'rest' fields")

    # Contact format
    validate_contact(data.get("contact", ""), path)

    # Sections
    sections = data.get("sections")
    if not isinstance(sections, list) or not sections:
        fail(f"{path} sections must be a non-empty list")

    has_experience = False
    for i, sec in enumerate(sections):
        sec_missing = REQUIRED_SECTION_KEYS - set(sec.keys())
        if sec_missing:
            fail(f"{path} sections[{i}] missing keys: {sorted(sec_missing)}")

        sec_type = sec.get("type")

        if sec_type == "summary":
            if not sec.get("body"):
                fail(f"{path} sections[{i}] (summary) must have a non-empty 'body'")

        elif sec_type == "capabilities":
            items = sec.get("items")
            if not isinstance(items, list) or not items:
                fail(f"{path} sections[{i}] (capabilities) must have a non-empty 'items' list")
            for j, it in enumerate(items):
                if not it.get("label") or not it.get("rest"):
                    fail(f"{path} sections[{i}].items[{j}] must have non-empty 'label' and 'rest'")

        elif sec_type == "experience":
            has_experience = True
            companies = sec.get("companies")
            if not isinstance(companies, list) or not companies:
                fail(f"{path} sections[{i}] (experience) must have a non-empty 'companies' list")
            for ci, co in enumerate(companies):
                co_missing = REQUIRED_COMPANY_KEYS - set(co.keys())
                if co_missing:
                    fail(f"{path} sections[{i}].companies[{ci}] missing keys: {sorted(co_missing)}")
                roles = co.get("roles")
                if not isinstance(roles, list) or not roles:
                    fail(
                        f"{path} sections[{i}].companies[{ci}] "
                        f"('{co.get('name', '?')}') must have a non-empty 'roles' list"
                    )
                for ri, role in enumerate(roles):
                    role_missing = REQUIRED_ROLE_KEYS - set(role.keys())
                    if role_missing:
                        fail(
                            f"{path} sections[{i}].companies[{ci}].roles[{ri}] "
                            f"missing keys: {sorted(role_missing)}"
                        )
                    bullets = role.get("bullets")
                    if not isinstance(bullets, list) or not bullets:
                        fail(
                            f"{path} sections[{i}].companies[{ci}].roles[{ri}] "
                            f"('{role.get('title', '?')}') must have a non-empty 'bullets' list"
                        )

        elif sec_type == "lines":
            lines = sec.get("lines")
            if not isinstance(lines, list):
                fail(f"{path} sections[{i}] (lines) must have a 'lines' list")

    if not has_experience:
        fail(f"{path} must contain at least one section with type 'experience'")


def validate_cover(data: dict, path: Path) -> None:
    missing = REQUIRED_COVER_KEYS - set(data.keys())
    if missing:
        fail(f"{path} missing required keys: {sorted(missing)}")
    if not isinstance(data.get("paragraphs"), list) or not data["paragraphs"]:
        fail(f"{path} must contain a non-empty paragraphs list")
    if data.get("valediction") != "Sincerely,":
        fail(f"{path} valediction must be exactly 'Sincerely,'")
    validate_contact(data.get("contact", ""), path)


def main() -> None:
    if len(sys.argv) != 2:
        fail(
            "Usage: scripts/validate_application.py "
            "'~/Documents/Resumes/Company - Role Title'"
        )

    job_folder = Path(sys.argv[1]).expanduser()
    resources = job_folder / "Resources"
    if not resources.exists():
        fail(f"Missing Resources folder: {resources}")

    resume_path = resources / "resume-content.json"
    cover_path = resources / "cover-letter-content.json"

    validate_resume(load_json(resume_path), resume_path)
    validate_cover(load_json(cover_path), cover_path)

    print("Application JSON validation passed.")


if __name__ == "__main__":
    main()
