/*
 * cover_letter_style.js  —  Reusable cover letter DOCX renderer.
 *
 * Styling is FIXED in this file. Content is supplied by a JSON file so that
 * each new cover letter only changes the content, never the formatting.
 *
 * Usage:  node cover_letter_style.js <content.json> <output.docx>
 *
 * Content schema (see cover_letter_content_*.json):
 * {
 *   "name":       "Jane Doe",
 *   "contact":    "City, ST  |  email  |  phone  |  LinkedIn URL",
 *   "date":       "June 17, 2026",
 *   "salutation": "Dear Hiring Manager,",
 *   "paragraphs": [
 *     "Opening paragraph...",
 *     "Body paragraph...",
 *     "Closing paragraph..."
 *   ],
 *   "valediction": "Sincerely,",
 *   "signature":   "Jane Doe"
 * }
 *
 * Em-dash policy: em dashes are NEVER allowed in body paragraphs.
 * Use colons or restructure the sentence instead.
 */

const fs = require("fs");
const { Document, Packer, Paragraph, TextRun, AlignmentType, BorderStyle } = require("docx");

// ---- Design tokens (DO NOT change casually; these define the house style) ----
const FONT  = "Arial";
const NAVY  = "1F3864";  // name, divider rule
const GRAY  = "555555";  // contact line
const DARK  = "333333";  // all body text
const BLACK = "000000";  // candidate name

const SZ_NAME = 28;   // 14pt
const SZ_BODY = 22;   // 11pt

const MARGIN = 1080;  // 0.75"
const RULE = { bottom: { style: BorderStyle.SINGLE, size: 8, color: NAVY, space: 1 } };

function buildDoc(letter) {
  const doc = new Document({
    styles: {
      default: { document: { run: { font: FONT, size: SZ_BODY, color: DARK } } },
    },
    sections: [{
      properties: {
        page: {
          size: { width: 12240, height: 15840 },
          margin: { top: MARGIN, right: MARGIN, bottom: MARGIN, left: MARGIN },
        },
      },
      children: render(letter),
    }],
  });
  return doc;
}

function p(children, opts = {}) {
  return new Paragraph({ children, ...opts });
}

function t(text, opts = {}) {
  return new TextRun({ text, font: FONT, ...opts });
}

function render(letter) {
  const c = [];

  // ── Header ────────────────────────────────────────────────────────────────
  c.push(p([t(letter.name, { size: SZ_NAME, bold: true, color: BLACK })],
    { alignment: AlignmentType.CENTER, spacing: { before: 0, after: 30 } }));

  c.push(p([t(letter.contact, { size: SZ_BODY, color: GRAY })],
    { alignment: AlignmentType.CENTER, spacing: { before: 0, after: 50 } }));

  // Divider rule
  c.push(p([], { spacing: { before: 40, after: 60 }, border: RULE }));

  // ── Date ──────────────────────────────────────────────────────────────────
  c.push(p([t(letter.date, { size: SZ_BODY, color: GRAY })],
    { alignment: AlignmentType.LEFT, spacing: { before: 80, after: 60 } }));

  // ── Salutation ────────────────────────────────────────────────────────────
  c.push(p([t(letter.salutation, { size: SZ_BODY, color: DARK })],
    { alignment: AlignmentType.LEFT, spacing: { before: 0, after: 180 } }));

  // ── Body paragraphs ───────────────────────────────────────────────────────
  for (const para of letter.paragraphs) {
    c.push(p([t(para, { size: SZ_BODY, color: DARK })],
      { alignment: AlignmentType.LEFT, spacing: { before: 0, after: 180 } }));
  }

  // ── Valediction ───────────────────────────────────────────────────────────
  c.push(p([t(letter.valediction, { size: SZ_BODY, color: DARK })],
    { alignment: AlignmentType.LEFT, spacing: { before: 240, after: 60 } }));

  // ── Signature ─────────────────────────────────────────────────────────────
  c.push(p([t(letter.signature, { size: SZ_BODY, color: BLACK })],
    { alignment: AlignmentType.LEFT, spacing: { before: 0, after: 0 } }));

  return c;
}

// ---- Entry point ----
const contentPath = process.argv[2] || "cover_letter_content.json";
const outPath     = process.argv[3] || "cover_letter.docx";
const letter = JSON.parse(fs.readFileSync(contentPath, "utf-8"));
Packer.toBuffer(buildDoc(letter)).then(buf => {
  fs.writeFileSync(outPath, buf);
  console.log("written:", outPath);
});
