"""jobscout_apify_linkedin.py — Direct Apify REST LinkedIn scraper.

Replaces the agent-driven Apify MCP scrape with deterministic REST calls so the
LinkedIn scrape runs as a plain subprocess inside the alerts orchestrator (no
agent babysitting, no MCP). Mirrors the output contract of jobscout_wide_scraper.py
so the rest of the pipeline (prep/chunk/score/build) consumes it unchanged.

Flow per search (remote + Orange County, from config/jobscout_config.json):
  1. POST start an async actor run with the search block as input.
  2. Poll the run until SUCCEEDED/FAILED.
  3. Page through the run's dataset items (clean=true).
Then normalize -> dedup vs seen IDs + max-age -> write run/raw_linkedin.jsonl
(full, with descriptions) and run/scan_linkedin.jsonl (minimal, for seen tracking).

Usage:
  python3 jobscout_apify_linkedin.py \\
      --config     config/jobscout_config.json \\
      --seen       job-scout-seen.json \\
      --run-dir    run \\
      --token-file ~/.config/job-scout/apify_token \\
      --max-age-days 7
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

ACTOR = "curious_coder~linkedin-jobs-scraper"
API = "https://api.apify.com/v2"
REQUEST_TIMEOUT = 60
POLL_INTERVAL = 15          # seconds between run-status polls
POLL_MAX = 25 * 60          # give up after 25 min per run
PAGE = 500                  # dataset items per page


def _strip_html(html: str) -> str:
    if not html:
        return ""
    text = re.sub(r"<[^>]+>", " ", html)
    for ent, rep in [("&nbsp;", " "), ("&amp;", "&"), ("&lt;", "<"),
                     ("&gt;", ">"), ("&quot;", '"'), ("&#39;", "'")]:
        text = text.replace(ent, rep)
    return re.sub(r"\s+", " ", text).strip()


def _load_seen_ids(seen_path: Path) -> set:
    if not seen_path.exists():
        return set()
    try:
        data = json.loads(seen_path.read_text())
        if isinstance(data, list):
            return set(str(x) for x in data)
        return set(str(x) for x in data.get("ids", []))
    except Exception:
        return set()


def _write_jsonl(path: Path, records: list) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for rec in records:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def _cutoff_date(max_age_days: int) -> str:
    return (datetime.now(tz=timezone.utc) - timedelta(days=max_age_days)).date().isoformat()


# ---------------------------------------------------------------------------
# Apify REST helpers
# ---------------------------------------------------------------------------

def _req(method: str, url: str, token: str, body: dict | None = None):
    sep = "&" if "?" in url else "?"
    full = f"{url}{sep}token={urllib.parse.quote(token)}"
    data = json.dumps(body).encode() if body is not None else None
    headers = {"Content-Type": "application/json"} if body is not None else {}
    req = urllib.request.Request(full, data=data, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT) as r:
        raw = r.read()
        return json.loads(raw) if raw else {}


def _start_run(token: str, search_input: dict) -> str:
    resp = _req("POST", f"{API}/acts/{ACTOR}/runs", token, body=search_input)
    run_id = resp.get("data", {}).get("id")
    if not run_id:
        raise RuntimeError(f"no run id in start response: {str(resp)[:200]}")
    return run_id


def _wait_run(token: str, run_id: str) -> dict:
    waited = 0
    while True:
        data = _req("GET", f"{API}/actor-runs/{run_id}", token).get("data", {})
        status = data.get("status")
        if status in ("SUCCEEDED",):
            return data
        if status in ("FAILED", "ABORTED", "TIMED-OUT"):
            raise RuntimeError(f"run {run_id} ended {status}")
        if waited >= POLL_MAX:
            raise RuntimeError(f"run {run_id} still {status} after {POLL_MAX}s — giving up")
        time.sleep(POLL_INTERVAL)
        waited += POLL_INTERVAL


def _fetch_items(token: str, dataset_id: str) -> list:
    items, offset = [], 0
    while True:
        url = f"{API}/datasets/{dataset_id}/items?clean=true&offset={offset}&limit={PAGE}"
        page = _req("GET", url, token)
        if not isinstance(page, list):
            break
        items.extend(page)
        if len(page) < PAGE:
            break
        offset += PAGE
    return items


# ---------------------------------------------------------------------------
# Normalization -> canonical pipeline record
# ---------------------------------------------------------------------------

def _normalize(item: dict, label: str, now_iso: str) -> dict | None:
    jid = str(item.get("id") or item.get("jobId") or "").strip()
    if not jid:
        return None
    title = item.get("title") or ""
    company = item.get("companyName") or item.get("company") or ""
    desc = item.get("descriptionText") or _strip_html(item.get("descriptionHtml") or "")
    posted = (item.get("postedAt") or item.get("publishedAt") or "")[:10]
    url = item.get("link") or item.get("jobUrl") or item.get("applyUrl") or ""
    return {
        "id": jid,
        "title": title,
        "companyName": company,
        "company": company,
        "location": item.get("location") or "",
        "postedAt": posted,
        "posted": posted,
        "link": url,
        "source": "linkedin",
        "_src": "alerts",
        "addedAt": now_iso,
        "workplace": "Remote" if label == "remote" else "",
        "salary": item.get("salary") or "",
        "description": desc,
    }


def _build_actor_input(block: dict) -> dict:
    """The actor expects a LinkedIn job-search URL, not raw fields. Build it from
    the config search block (keywords/location/f_WT/f_TPR/distance)."""
    params = {}
    if block.get("keywords"):
        params["keywords"] = block["keywords"]
    if block.get("location"):
        params["location"] = block["location"]
    for k in ("f_WT", "f_TPR", "distance"):
        if block.get(k) not in (None, ""):
            params[k] = block[k]
    params["position"] = 1
    params["pageNum"] = 0
    url = "https://www.linkedin.com/jobs/search/?" + urllib.parse.urlencode(params)
    return {"urls": [url], "count": int(block.get("count", 200)),
            "scrapeCompany": False, "splitByLocation": False}


def _scrape_search(token: str, label: str, search_input: dict, now_iso: str):
    """Returns (records, raw_item_count, requested_cap)."""
    actor_input = _build_actor_input(search_input)
    cap = int(actor_input.get("count", 0))
    print(f"[apify] starting LinkedIn '{label}' run…", flush=True)
    run_id = _start_run(token, actor_input)
    print(f"[apify] '{label}' run {run_id} started; polling…", flush=True)
    run = _wait_run(token, run_id)
    ds = run.get("defaultDatasetId")
    items = _fetch_items(token, ds) if ds else []
    raw = len(items)
    hit = " [CAP HIT]" if cap and raw >= cap else ""
    print(f"[apify] '{label}' SUCCEEDED — {raw} raw items (cap {cap}){hit}", flush=True)
    out = []
    for it in items:
        rec = _normalize(it, label, now_iso)
        if rec:
            out.append(rec)
    return out, raw, cap


def main() -> int:
    ap = argparse.ArgumentParser(description="Direct Apify REST LinkedIn scraper.")
    ap.add_argument("--config", required=True)
    ap.add_argument("--seen", required=True)
    ap.add_argument("--run-dir", default="run")
    ap.add_argument("--token-file", default=os.path.expanduser("~/.config/job-scout/apify_token"))
    ap.add_argument("--max-age-days", type=int, default=7)
    ap.add_argument("--max-age-hours", type=int, default=36,
                    help="Lookback window in hours (default 36; overrides --max-age-days when > 0)")
    args = ap.parse_args()

    token = os.environ.get("APIFY_TOKEN", "").strip()
    if not token:
        tf = Path(os.path.expanduser(args.token_file))
        if not tf.exists():
            print(f"[apify] ERROR: no token (env APIFY_TOKEN unset and {tf} missing)", file=sys.stderr)
            return 2
        token = tf.read_text().strip()

    cfg = json.loads(Path(args.config).read_text()) if Path(args.config).exists() else {}
    searches = cfg.get("search", {})
    remote = searches.get("remote")
    oc = searches.get("orangeCounty")

    run_dir = Path(args.run_dir)
    run_dir.mkdir(parents=True, exist_ok=True)
    seen_ids = _load_seen_ids(Path(args.seen))
    if args.max_age_hours > 0:
        cutoff = (datetime.now(tz=timezone.utc) - timedelta(hours=args.max_age_hours)).date().isoformat()
    else:
        cutoff = _cutoff_date(args.max_age_days)
    now_iso = datetime.now(tz=timezone.utc).isoformat()

    all_jobs: list = []
    failures = 0
    saturated: list = []
    for label, search_input in (("remote", remote), ("oc", oc)):
        if not search_input:
            print(f"[apify] no '{label}' search block in config — skipping", file=sys.stderr)
            continue
        try:
            recs, raw, cap = _scrape_search(token, label, dict(search_input), now_iso)
            all_jobs.extend(recs)
            if cap and raw >= cap:
                saturated.append({"label": label, "raw": raw, "cap": cap})
                print(f"[apify] SATURATION: '{label}' hit the {cap} cap "
                      f"({raw} returned) — split this query", file=sys.stderr)
        except Exception as e:
            failures += 1
            print(f"[apify] '{label}' scrape FAILED: {e}", file=sys.stderr)

    # Record cap saturation so the orchestrator can post a Slack heads-up.
    if saturated:
        (run_dir / "linkedin_saturation.json").write_text(json.dumps(saturated, indent=2))

    # Dedup (across the two searches), then filter by seen + max-age.
    seen_local, deduped = set(), []
    for j in all_jobs:
        if j["id"] in seen_local:
            continue
        seen_local.add(j["id"])
        if j["id"] in seen_ids:
            continue
        if j["postedAt"] and j["postedAt"] < cutoff:
            continue
        deduped.append(j)

    scan_records = [
        {"id": j["id"], "title": j["title"], "companyName": j["companyName"],
         "location": j["location"], "postedAt": j["postedAt"], "_src": "alerts",
         "source": j["source"]}
        for j in deduped
    ]
    _write_jsonl(run_dir / "raw_linkedin.jsonl", deduped)
    _write_jsonl(run_dir / "scan_linkedin.jsonl", scan_records)
    print(f"[apify] wrote {len(deduped)} new LinkedIn jobs "
          f"({run_dir}/raw_linkedin.jsonl, scan_linkedin.jsonl)")

    # Non-zero exit only if BOTH searches errored (lets the orchestrator decide).
    if failures and not deduped:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
