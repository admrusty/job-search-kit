"""jobscout_keyword_report.py — keyword relevance report with light stemming.

Attributes scored jobs to the config search keywords using stem-phrase matching,
so "knowledge manager" credits "Knowledge Management" roles (LinkedIn stems/expands
terms, so literal substring matching undercounts). Reports, per keyword:
job count, average score, and # scoring >=7. Free — local, no extra scraping.

Usage:
  python3 jobscout_keyword_report.py [--data data.json] [--config config/jobscout_config.json]
      [--source all|linkedin|greenhouse|lever|ashby] [--since YYYY-MM-DD]
      [--out reports/keyword_relevance.md]
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

# Light suffix-stripping stemmer — longest suffixes first; collapses e.g.
# manager/management -> "manag", operations/operation -> "oper".
_SUF = ("izations", "ization", "ements", "ement", "ations", "ation",
        "ings", "ing", "ers", "er", "ies", "ied", "ment", "es", "ed", "ly", "s")


def _stem(w: str) -> str:
    for s in _SUF:
        if w.endswith(s) and len(w) - len(s) >= 3:
            w = w[:-len(s)]
            break
    if len(w) > 3 and w.endswith("e"):
        w = w[:-1]
    return w


def _stem_text(t: str) -> str:
    return " ".join(_stem(tok) for tok in re.findall(r"[a-z0-9]+", (t or "").lower()))


def _load_jobs(data_path: str) -> list:
    d = json.loads(Path(data_path).read_text())
    jobs = d if isinstance(d, list) else d.get("jobs", d)
    if isinstance(jobs, dict):
        jobs = list(jobs.values())
    return jobs


def build_rows(jobs, keywords):
    kw_stem = {k: _stem_text(k) for k in keywords}
    for j in jobs:
        j["_blob"] = _stem_text((j.get("title") or "") + " " + (j.get("description") or ""))
    rows = []
    for k in keywords:
        ks = kw_stem[k]
        m = [j for j in jobs if ks and ks in j["_blob"]]
        if m:
            avg = sum((j.get("score") or 0) for j in m) / len(m)
            hi = sum(1 for j in m if (j.get("score") or 0) >= 7)
            top = max(m, key=lambda j: (j.get("score") or 0))
            rows.append((k, len(m), round(avg, 1), hi, (top.get("title") or "")[:40]))
        else:
            rows.append((k, 0, 0.0, 0, ""))
    rows.sort(key=lambda r: (-r[3], -r[2], -r[1]))
    return rows


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default="data.json")
    ap.add_argument("--config", default="config/jobscout_config.json")
    ap.add_argument("--source", default="all")
    ap.add_argument("--since", default="")
    ap.add_argument("--out", default="")
    a = ap.parse_args()

    cfg = json.loads(Path(a.config).read_text())
    keywords = [k.strip().strip('"') for k in cfg["search"]["remote"]["keywords"].split(" OR ")]

    jobs = []
    for j in _load_jobs(a.data):
        if a.source != "all" and j.get("source") != a.source:
            continue
        if a.since and str(j.get("addedAt", ""))[:10] < a.since:
            continue
        jobs.append(j)

    rows = build_rows(jobs, keywords)

    scope = a.source + (f" since {a.since}" if a.since else "")
    lines = [f"# Keyword relevance — {scope} ({len(jobs)} jobs)", "",
             "Stem-phrase matched against title+description. Sorted by # scoring >=7.", "",
             "| keyword | jobs | avg score | #>=7 | top hit |",
             "|---|---:|---:|---:|---|"]
    for k, n, avg, hi, top in rows:
        lines.append(f"| {k} | {n} | {avg} | {hi} | {top} |")
    report = "\n".join(lines)
    print(report)
    if a.out:
        Path(a.out).parent.mkdir(parents=True, exist_ok=True)
        Path(a.out).write_text(report + "\n")
        print(f"\n[report] written -> {a.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
