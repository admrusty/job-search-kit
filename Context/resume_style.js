/*
 * resume_style.js  —  Reusable resume DOCX renderer.
 *
 * Styling is FIXED in this file. Content is supplied by a JSON file so that
 * each new resume only changes the content, never the formatting.
 *
 * Usage:  node resume_style.js <content.json> <output.docx>
 *
 * Content schema (see resume_content_*.json):
 * {
 *   "name": "Jane Doe",
 *   "contact": "City, ST  |  email  |  phone  |  LinkedIn URL",
 *   "headline": { "bold": "Primary Title", "rest": "Descriptor, Descriptor, Descriptor" },
 *   "sections": [
 *     { "type": "summary",      "heading": "SUMMARY", "body": "..." },
 *     { "type": "capabilities", "heading": "CORE CAPABILITIES",
 *       "items": [ { "label": "Group", "rest": "a, b, c" } ] },
 *     { "type": "experience",   "heading": "PROFESSIONAL EXPERIENCE",
 *       "companies": [
 *         { "name": "Company", "location": "Remote", "note": "optional italic note",
 *           "roles": [
 *             { "title": "Title", "dates": "Mon YYYY \u2013 Mon YYYY",
 *               "context": "optional italic context line",
 *               "bullets": [ "bullet one", "bullet two" ] }
 *         ] }
 *       ] },
 *     { "type": "lines",        "heading": "EDUCATION", "lines": [ "line one" ] },
 *     { "type": "capabilities", "heading": "TOOLS & PLATFORMS",
 *       "items": [ { "label": "Group", "rest": "a, b, c" } ] }
 *   ]
 * }
 *
 * Em-dash policy: em dashes are allowed in structural lines (company/location,
 * Education, Certifications) but NEVER in prose, summaries, or bullets.
 */
 
const fs = require("fs");
const {
  Document, Packer, Paragraph, TextRun, AlignmentType, LevelFormat,
  BorderStyle, TabStopType, TabStopPosition,
} = require("docx");
 
// ---- Design tokens (DO NOT change casually; these define the house style) ----
const FONT = "Arial";
const NAVY = "1F3864";   // headings, company names, headline
const GRAY = "555555";   // contact, dates, location
const DARK = "333333";   // body, bullets, capabilities, context
const BLACK = "000000";  // name, role titles
 
// half-point sizes
const SZ_NAME = 28;      // 14pt
const SZ_HEADLINE = 22;  // 11pt
const SZ_HEADING = 24;   // 12pt
const SZ_COMPANY = 22;   // 11pt
const SZ_BODY = 22;      // 11pt
 
const MARGIN = 1080;             // 0.75"
const RIGHT_TAB = 10080;         // 12240 - margin - margin
const RULE = { bottom: { style: BorderStyle.SINGLE, size: 8, color: NAVY, space: 1 } };
 
function buildDoc(resume) {
  const doc = new Document({
    styles: {
      default: { document: { run: { font: FONT, size: SZ_BODY, color: DARK } } },
      paragraphStyles: [
        { id: "ResumeName", name: "Resume Name", basedOn: "Normal", next: "Normal",
          run: { font: FONT, size: SZ_NAME, bold: true, color: BLACK },
          paragraph: { alignment: AlignmentType.CENTER, spacing: { before: 0, after: 30 } } },
        { id: "ResumeContact", name: "Resume Contact", basedOn: "Normal", next: "Normal",
          run: { font: FONT, size: SZ_BODY, color: GRAY },
          paragraph: { alignment: AlignmentType.CENTER, spacing: { before: 0, after: 40 } } },
        { id: "ResumeHeadline", name: "Resume Headline", basedOn: "Normal", next: "Normal",
          run: { font: FONT, size: SZ_HEADLINE, color: NAVY },
          paragraph: { alignment: AlignmentType.CENTER, spacing: { before: 0, after: 50 } } },
        { id: "ResumeDivider", name: "Resume Divider", basedOn: "Normal", next: "Normal",
          run: { font: FONT, size: SZ_BODY },
          paragraph: { spacing: { before: 60, after: 60 }, border: RULE } },
        { id: "ResumeHeading", name: "Resume Heading", basedOn: "Normal", next: "Normal",
          run: { font: FONT, size: SZ_HEADING, bold: true, color: NAVY },
          paragraph: { spacing: { before: 120, after: 40 }, keepNext: true } },
        { id: "ResumeCompany", name: "Resume Company", basedOn: "Normal", next: "Normal",
          run: { font: FONT, size: SZ_COMPANY, bold: true, color: NAVY },
          paragraph: { spacing: { before: 100, after: 10 }, keepNext: true } },
        { id: "ResumeRole", name: "Resume Role", basedOn: "Normal", next: "Normal",
          run: { font: FONT, size: SZ_BODY, bold: true, color: BLACK },
          paragraph: { spacing: { before: 0, after: 20 }, keepNext: true,
            tabStops: [{ type: TabStopType.RIGHT, position: RIGHT_TAB }] } },
        { id: "ResumeContext", name: "Resume Context", basedOn: "Normal", next: "Normal",
          run: { font: FONT, size: SZ_BODY, italics: true, color: DARK },
          paragraph: { spacing: { before: 0, after: 20 } } },
        { id: "ResumeBody", name: "Resume Body", basedOn: "Normal", next: "Normal",
          run: { font: FONT, size: SZ_BODY, color: DARK },
          paragraph: { alignment: AlignmentType.LEFT, spacing: { before: 0, after: 20 } } },
        { id: "ResumeBullet", name: "Resume Bullet", basedOn: "Normal", next: "Normal",
          run: { font: FONT, size: SZ_BODY, color: DARK },
          paragraph: { alignment: AlignmentType.LEFT, spacing: { before: 0, after: 30 } } },
      ],
    },
    numbering: {
      config: [{ reference: "bullets", levels: [{ level: 0, format: LevelFormat.BULLET,
        text: "\u2022", alignment: AlignmentType.LEFT,
        style: { paragraph: { indent: { left: 288, hanging: 187 } } } }] }],
    },
    sections: [{
      properties: { page: {
        size: { width: 12240, height: 15840 },
        margin: { top: MARGIN, right: MARGIN, bottom: MARGIN, left: MARGIN },
      } },
      children: render(resume),
    }],
  });
  return doc;
}
 
function render(resume) {
  const c = [];
  c.push(new Paragraph({ style: "ResumeName", children: [new TextRun(resume.name)] }));
  c.push(new Paragraph({ style: "ResumeContact", children: [new TextRun(resume.contact)] }));
  c.push(new Paragraph({ style: "ResumeHeadline", children: [
    new TextRun({ text: resume.headline.bold, bold: true }),
    new TextRun({ text: "  |  " + resume.headline.rest, bold: false }),
  ]}));
 
  for (const sec of resume.sections) {
    c.push(new Paragraph({ style: "ResumeDivider", children: [] }));      // rule above heading
    c.push(new Paragraph({ style: "ResumeHeading", children: [new TextRun(sec.heading)] }));
 
    if (sec.type === "summary") {
      c.push(new Paragraph({ style: "ResumeBody", children: [new TextRun(sec.body)] }));
    } else if (sec.type === "capabilities") {
      for (const it of sec.items) {
        c.push(new Paragraph({ style: "ResumeBody", children: [
          new TextRun({ text: it.label + ": ", bold: true }),
          new TextRun({ text: it.rest, bold: false }),
        ]}));
      }
    } else if (sec.type === "lines") {
      for (const ln of sec.lines) {
        c.push(new Paragraph({ style: "ResumeBody", children: [new TextRun(ln)] }));
      }
    } else if (sec.type === "experience") {
      for (const co of sec.companies) {
        c.push(new Paragraph({ style: "ResumeCompany", children: [
          new TextRun(co.name),
          new TextRun({ text: "  \u2014  " + co.location, bold: false, color: GRAY, size: SZ_BODY }),
        ]}));
        if (co.note) c.push(new Paragraph({ style: "ResumeContext", children: [new TextRun(co.note)] }));
        for (const role of co.roles) {
          c.push(new Paragraph({ style: "ResumeRole", children: [
            new TextRun(role.title),
            new TextRun({ text: "\t" + role.dates, bold: false, color: GRAY, size: SZ_BODY }),
          ]}));
          if (role.context) c.push(new Paragraph({ style: "ResumeContext", children: [new TextRun(role.context)] }));
          for (const b of role.bullets) {
            c.push(new Paragraph({ style: "ResumeBullet", numbering: { reference: "bullets", level: 0 },
              children: [new TextRun(b)] }));
          }
        }
      }
    }
  }
  return c;
}
 
function checkOverflow(resume) {
  let bullets = 0;
  let words = 0;
  const countWords = s => s.trim().split(/\s+/).filter(Boolean).length;

  words += countWords(resume.name || "");
  words += countWords(resume.contact || "");
  words += countWords((resume.headline.bold || "") + " " + (resume.headline.rest || ""));

  for (const sec of resume.sections) {
    words += countWords(sec.heading || "");
    if (sec.type === "summary") {
      words += countWords(sec.body || "");
    } else if (sec.type === "capabilities") {
      for (const it of sec.items) words += countWords((it.label || "") + " " + (it.rest || ""));
    } else if (sec.type === "lines") {
      for (const ln of sec.lines) words += countWords(ln);
    } else if (sec.type === "experience") {
      for (const co of sec.companies) {
        words += countWords(co.name || "");
        if (co.note) words += countWords(co.note);
        for (const role of co.roles) {
          words += countWords(role.title || "");
          if (role.context) words += countWords(role.context);
          for (const b of role.bullets) {
            bullets++;
            words += countWords(b);
          }
        }
      }
    }
  }

  if (bullets > 16 || words > 700) {
    process.stderr.write(
      `WARNING: resume may exceed 2 pages (${bullets} bullets, ~${words} words) — review the DOCX before submitting\n`
    );
  }
}

// ---- entry point ----
const contentPath = process.argv[2] || "resume_content.json";
const outPath = process.argv[3] || "resume.docx";
const resume = JSON.parse(fs.readFileSync(contentPath, "utf-8"));
Packer.toBuffer(buildDoc(resume)).then(buf => {
  fs.writeFileSync(outPath, buf);
  console.log("written:", outPath);
  checkOverflow(resume);
});
 