# Master Profile — TEMPLATE

> **What this file is.** The Master Profile is the single source of truth for every
> verified fact about you: your identity, work history, accomplishments, metrics,
> education, certifications, tools, and the boundaries on how strongly each claim
> may be stated. Every agent in this workspace reads it at runtime and is
> forbidden from inventing anything not recorded here.
>
> **How to use this template.** Run `/setup`, which walks you through filling this
> in and saves the result as `Context/Master Profile.md` (the runtime filename the
> agents expect). You can also fill it in by hand: copy this file to
> `Context/Master Profile.md` and replace every `{{PLACEHOLDER}}` with your real
> information. Delete instruction lines (the `>` notes) as you go. Keep anything
> you cannot verify out of this file — if it is not here, the resume cannot claim it.

---

## 1. Contact / Identity

> The header of every resume and cover letter is built from these fields. Use the
> exact spelling and formatting you want to appear on the documents.

- **Full name:** {{FULL_NAME}}
- **Email:** {{EMAIL}}
- **Phone:** {{PHONE}}
- **Location:** {{CITY_STATE}}  <!-- e.g. "Austin, TX" or "Remote (US)". City/state only — no street address. -->
- **LinkedIn:** {{LINKEDIN_URL}}
- **Portfolio / website (optional):** {{PORTFOLIO_URL}}

**Canonical contact line** (copy this exact string into resume/cover-letter contact fields):

`{{CITY_STATE}}  |  {{EMAIL}}  |  {{PHONE}}  |  {{LINKEDIN_URL}}`

---

## 2. Professional Summary / Positioning

> This is who you are at a glance and the kinds of roles you want to be routed
> toward. Agents use it to decide which "lane" to emphasize and how senior to
> pitch the resume. Be specific about the function, not just the industry.

- **Target role families (in priority order):** {{TARGET_ROLES}}
  <!-- e.g. "Knowledge Operations, Content Strategy, Technical Program Management" -->
- **Seniority level:** {{SENIORITY}}  <!-- IC / Senior IC / Manager / Director / Executive -->
- **One-paragraph positioning statement:**

  {{POSITIONING_PARAGRAPH}}
  <!-- 2–4 sentences. What business problem do you solve? What systems do you build
       or improve? What evidence shows you are effective? Avoid "results-driven",
       "passionate", "self-starter". -->

- **Positioning anchors** (the 3–5 themes your strongest evidence supports — agents reference these, do not invent new ones):
  - {{ANCHOR_1}}
  - {{ANCHOR_2}}
  - {{ANCHOR_3}}

---

## 3. Work History

> List roles in reverse-chronological order (most recent first). Create one block
> per role. If you held multiple titles at the same employer, give each its own
> block with accurate title and dates — do not merge promotions. Every
> accomplishment should be something you can defend in an interview, with a real
> metric wherever you have one.

### Role 1 (most recent)

- **Company:** {{COMPANY}}
- **Title:** {{TITLE}}
- **Location:** {{ROLE_LOCATION}}  <!-- city/state or "Remote" -->
- **Dates:** {{START_DATE}} – {{END_DATE}}  <!-- e.g. "Mar 2022 – Present" -->
- **Scope / context (optional, one line):** {{ROLE_CONTEXT}}
  <!-- team size, budget, org, mandate — only if it adds signal -->
- **Accomplishments** (action + scope + mechanism + result; lead with a strong verb; include a metric when real):
  - {{ACCOMPLISHMENT_1}}
  - {{ACCOMPLISHMENT_2}}
  - {{ACCOMPLISHMENT_3}}
  - {{ACCOMPLISHMENT_4}}

### Role 2

- **Company:** {{COMPANY}}
- **Title:** {{TITLE}}
- **Location:** {{ROLE_LOCATION}}
- **Dates:** {{START_DATE}} – {{END_DATE}}
- **Accomplishments:**
  - {{ACCOMPLISHMENT_1}}
  - {{ACCOMPLISHMENT_2}}

### Role 3

- **Company:** {{COMPANY}}
- **Title:** {{TITLE}}
- **Location:** {{ROLE_LOCATION}}
- **Dates:** {{START_DATE}} – {{END_DATE}}
- **Accomplishments:**
  - {{ACCOMPLISHMENT_1}}

> Add or remove role blocks as needed. Older or lightly relevant roles can be
> compressed to one line each, or grouped under an "Earlier Experience" heading.

---

## 4. Education

> One entry per degree. Include only what you actually earned.

- {{DEGREE}}, {{FIELD}} — {{INSTITUTION}}, {{LOCATION}} ({{YEAR}})
- {{DEGREE}}, {{FIELD}} — {{INSTITUTION}}, {{LOCATION}} ({{YEAR}})

---

## 5. Certifications

> Record the exact certification name, issuer, and date. Agents render these
> verbatim — they will not paraphrase or combine them. Leave this empty if you
> have none.

- {{CERTIFICATION_NAME}} — {{ISSUER}} ({{DATE}})
- {{CERTIFICATION_NAME}} — {{ISSUER}} ({{DATE}})

---

## 6. Skills / Tools

> Group related skills so they can become tidy resume capability lines. Only list
> things you can credibly discuss. Mark depth honestly where it matters.

- **{{SKILL_GROUP_1}}:** {{skill, skill, skill}}
- **{{SKILL_GROUP_2}}:** {{skill, skill, skill}}
- **{{SKILL_GROUP_3}}:** {{skill, skill, skill}}
- **{{SKILL_GROUP_4}}:** {{skill, skill, skill}}

---

## 7. Claim Calibration

> This section sets the guardrails the truth-auditor and reviewers enforce. It
> tells the agents how strongly each kind of claim may be stated so the resume
> never overstates your role. Be honest here — it protects you in interviews.

- **Owned vs. contributed:** List work you led/owned solely versus work where you
  were a key contributor or partner. Agents will use "led/built/designed" only for
  owned work and softer verbs ("partnered with", "contributed to", "supported")
  for the rest.
  - Owned: {{OWNED_WORK}}
  - Contributed / partnered: {{CONTRIBUTED_WORK}}

- **Metrics that are exact vs. approximate:** Note which numbers are precise and
  which are estimates (agents will prefix estimates with "approximately").
  - Exact: {{EXACT_METRICS}}
  - Approximate: {{APPROX_METRICS}}

- **Tool depth boundaries:** Tools where you have hands-on depth vs. exposure /
  adjacent / stakeholder-only familiarity. Agents will not imply hands-on expertise
  for exposure-only tools.
  - Hands-on: {{HANDS_ON_TOOLS}}
  - Exposure / adjacent only: {{EXPOSURE_TOOLS}}

- **Off-limits framings:** Claims that must never be made because they overstate
  your background (e.g. titles you never held, technical ownership you did not have).
  - {{OFF_LIMITS_1}}
  - {{OFF_LIMITS_2}}

- **Compensation context (optional, never used to screen):** {{COMP_NOTE}}
  <!-- Optional. Used only to help fill salary fields on applications. Leave blank to omit. -->

---

> **Reminder:** When new facts surface during a job search (a new metric, a new
> project, a corrected date), do not slip them silently into a resume. Add them
> here first so every future application stays consistent and defensible.
