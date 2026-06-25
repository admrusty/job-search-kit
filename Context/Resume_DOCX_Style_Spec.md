Resume DOCX Style Spec
This is the source of truth for how the candidate's resumes are formatted as Word
documents. It pairs with two working files:
resume_style.js — the renderer. Styling lives here and does not change per role.
resume_content_<role>.json — the content for one resume. This is the only file
that changes per application.
To produce a resume: update or create a content JSON, then run
node resume_style.js resume_content_<role>.json "Output Name.docx".
Page setup
Paper: US Letter, 8.5 x 11 in (12240 x 15840 DXA).
Margins: 0.75 in on all sides (1080 DXA).
Content width / right tab stop: 10080 DXA.
Font: Arial throughout.
Every paragraph uses a named paragraph style (below) so pressing Enter in Word or
LibreOffice inherits the right size, not the app default.
Do not use characterSpacing. Do not use the allCaps run property. Section headings
are typed in capital letters literally.
Color palette
Navy 1F3864: section headings, company names, headline, divider rules.
Gray 555555: contact line, role dates, company location.
Dark 333333: body text, bullets, capability lines, context and note lines.
Black 000000: candidate name, role titles.
Type sizes (body = 11pt minimum)
Name: 14pt, black, bold, centered.
Contact line: 11pt, gray, centered.
Headline: 11pt, navy, centered. Primary title bold; descriptor after the pipe regular weight.
Section heading: 12pt, navy, bold.
Company name: 11pt, navy, bold. Location follows in 11pt gray.
Role title: 11pt, black, bold. Dates follow in 11pt gray.
Body, bullets, capabilities, context: 11pt.
Structure and ordering
Header block (centered): Name, Contact, Headline.
Sections, each separated by a navy divider rule that sits above the heading.
The heading text itself is not underlined.
Default section order: Summary, Core Capabilities, Professional Experience,
Education, Certifications, then an optional bottom skills/tools section.
Section formatting
Divider rule: a thin empty paragraph carrying a navy bottom border
(single, size 8, space 1), placed before every section heading.
Summary: one paragraph of body text.
Capabilities / Skills / Tools (label lists): one line per group. Text before the
colon is bold; the list after the colon is regular weight. Separate items with
commas, never vertical pipes.
Experience:
Company line: company name navy bold, then   —  Location in gray.
Optional company note in italic dark gray (for example, a promotion summary).
Role line: title bold black on the left; dates gray, right-aligned to the margin
via a right tab stop at 10080. Title and dates share one line.
Optional role context in italic dark gray.
Bullets: real Word bullets (not typed glyphs), hanging indent.
Education / Certifications: one line per entry.
Em-dash policy
Em dashes are allowed in structural lines only: company-to-location separators,
Education lines, and Certification lines. Never use em dashes in prose, summaries,
or bullets. Use commas, or restructure the sentence, instead.
Certifications format
Render one structural line per certification, with an em dash separating the
issuer and date (em dashes are allowed in structural lines, per the policy above).
Use the exact certification text, issuer, and dates from the Master Profile
(Section 6) as the single source of truth; do not restate the literal values here.
Do not collapse these into a single generic certification unless explicitly asked.
The bottom skills section is flexible
The bottom section (heading and groupings) varies by resume and role. It can be
omitted, renamed (for example, "Skills" or "Tools & Platforms"), and regrouped to fit
the target job. Keep it to label lines using the capability format above.
Length
Two pages maximum for senior or lead-level resumes.
Bullet counts are diagnostics, not gates. Weight bullets toward the most recent, most relevant role. The binding constraints are the two-page maximum and a balanced layout — no sparse trailing page, no orphaned headings or dangling bullets. Compress before output rather than shrinking font or margins.