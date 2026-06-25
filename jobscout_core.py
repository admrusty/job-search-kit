"""Job Scout — canonical parser (single source of truth).

Derives a job's attributes from ACTUAL evidence (description text, location,
workplace fields), never from which search it came from. Search provenance
(`src`) is used ONLY to apply an honest "Remote — unverified" hedge when the
evidence is inconclusive — never to assert "Remote".
"""
import re


def home_city(default: str = "") -> str:
    """Return the configured home city (geo.homeCity) from jobscout_config.json.

    Used only for human-readable labels (e.g. commute origin in docstrings/logs).
    Falls back to ``default`` (empty string) when config is missing or unset, so
    no personal value is baked into the engine.
    """
    import json, os
    cfg_path = os.path.join(os.path.dirname(__file__), "config", "jobscout_config.json")
    try:
        with open(cfg_path) as f:
            return (json.load(f).get("geo", {}).get("homeCity") or default)
    except Exception:
        return default


# ---------------------------------------------------------------------------
# Canonical job schema
# Every job object in data.json and run/scored.json should conform to this.
# Fields not yet populated by the pipeline are set to None or a safe default.
# ---------------------------------------------------------------------------
CANONICAL_JOB_FIELDS = {
    # --- Identity ---
    "id": None,                        # LinkedIn job ID (string)
    "source": "linkedin",             # Source platform
    "sourceRun": None,                # "remote" or "oc"
    "sourceOffset": None,             # 0-indexed position in Apify dataset

    # --- Job metadata ---
    "title": None,
    "company": None,
    "locationRaw": None,
    "postedAt": None,
    "linkedinUrl": None,
    "applyUrl": None,

    # --- Workplace ---
    "workplaceRaw": None,
    "workplaceNormalized": None,       # Remote / Hybrid / On-site / Remote — unverified

    # --- Description ---
    "description": None,
    "descriptionSourceField": None,    # "description" / "descriptionText" / "descriptionHtml"
    "descriptionFetched": False,
    "descriptionFetchStatus": None,    # "success" / "failure" / "unavailable" / "metadata-only"

    # --- Scoring ---
    "score": None,
    "scoreReason": None,
    "searchLane": None,               # One of the 10 lanes from the taxonomy
    "matchedKeywords": [],

    # --- Salary (raw + parsed) ---
    "salaryRaw": None,
    "salaryLow": None,
    "salaryHigh": None,
    "salaryType": None,                # "annual" / "hourly" / "unknown"
    "hourlyRateLow": None,
    "hourlyRateHigh": None,
    "annualizedLow": None,
    "annualizedHigh": None,
    "contractAdjustedLow": None,
    "contractAdjustedHigh": None,
    "salaryPassesBaseFloor": None,
    "salaryPassesTargetComp": None,
    "compRisk": None,                  # "none" / "unknown" / "top-of-range-only" / "below-floor" / "contract-verify"
    "incentives": [],                  # e.g. ["RSU", "Bonus", "ESPP"]

    # --- Geo / CA eligibility ---
    "remoteEligibility": None,         # "US-wide" / "CA eligible" / "state restricted" / "location restricted" / "unknown"
    "restrictedStates": [],
    "caEligible": None,                # True / False / None (unknown)
    "caExcluded": False,
    "geoFit": None,                    # True / False / None
    "geoFitReason": None,
    "commuteEstimateMinutes": None,
    "commuteUnknown": True,

    # --- Review / state ---
    "applied": False,
    "appliedAt": None,
    "starred": False,
    "userDismissed": False,
    "autoSuppressed": False,
    "suppressionReason": None,
    "reviewStatus": "new",             # new / reviewing / applied / saved / dismissed
    "reviewAction": None,              # apply / verify-remote / check-salary / review-manually / already-applied / below-comp-floor / ca-restricted / dismiss
    "manualNotes": None,

    # --- Target employer ---
    "targetEmployer": False,
    "targetEmployerName": "",

    # --- Timestamps ---
    "createdAt": None,
    "updatedAt": None,
}


# ---------------------------------------------------------------------------
# Source / platform registry
# Add entries here as new ingestion sources are wired up.
# Keys are the canonical source names stored in data.json.
# ---------------------------------------------------------------------------
KNOWN_SOURCES = {
    "linkedin":   {"label": "LinkedIn",  "color": "#0a66c2"},
    "indeed":     {"label": "Indeed",    "color": "#2164f3"},
    "glassdoor":  {"label": "Glassdoor", "color": "#0caa41"},
    "greenhouse": {"label": "Greenhouse","color": "#3d9970"},
    "lever":      {"label": "Lever",     "color": "#3c4fe0"},
    "workday":    {"label": "Workday",   "color": "#f8783a"},
    "ashby":      {"label": "Ashby",     "color": "#6c47ff"},
    "employer":   {"label": "Direct",    "color": "#64748b"},
}

# Domain fragments → canonical source name.
# Checked in order; first match wins.
_SOURCE_DOMAIN_MAP = [
    ("linkedin.com",           "linkedin"),
    ("indeed.com",             "indeed"),
    ("glassdoor.com",          "glassdoor"),
    ("greenhouse.io",          "greenhouse"),
    ("boards.greenhouse.io",   "greenhouse"),
    ("jobs.ashbyhq.com",       "ashby"),
    ("ashbyhq.com",            "ashby"),
    ("lever.co",               "lever"),
    ("jobs.lever.co",          "lever"),
    ("myworkdayjobs.com",      "workday"),
    ("wd1.myworkdayjobs.com",  "workday"),
    ("wd5.myworkdayjobs.com",  "workday"),
    ("paylocity.com",          "employer"),
]


def source_from_link(link: str) -> str:
    """Infer the canonical source name from a job URL.

    Returns a key from KNOWN_SOURCES, or "employer" for unrecognised ATS
    domains, or "" if the link is empty.
    """
    if not link:
        return ""
    link_lower = link.lower()
    for fragment, source in _SOURCE_DOMAIN_MAP:
        if fragment in link_lower:
            return source
    # Anything with a career/job path on an unrecognised domain is a direct employer page
    if any(kw in link_lower for kw in ("/careers/", "/jobs/", "/job/", "/openings/", "career")):
        return "employer"
    return ""


def normalize_job(raw: dict) -> dict:
    """Return a canonical job dict by merging raw fields into the schema.

    This is a shallow merge: known fields from raw are copied in;
    unrecognised fields are preserved under their original keys.
    Salary and geo fields are NOT parsed here — those are handled by
    extract_salary() and the geo functions respectively.
    The caller is responsible for populating computed fields after this call.
    """
    import datetime

    job = dict(CANONICAL_JOB_FIELDS)  # start from defaults

    # Copy all raw fields in (preserves any extra fields from Apify)
    job.update(raw)

    # Normalize description to the canonical field name
    job["description"] = get_description(raw)

    # Record which source field the description came from
    if raw.get("description"):
        job["descriptionSourceField"] = "description"
    elif raw.get("descriptionText"):
        job["descriptionSourceField"] = "descriptionText"
    elif raw.get("descriptionHtml"):
        job["descriptionSourceField"] = "descriptionHtml"
    else:
        job["descriptionSourceField"] = None

    job["descriptionFetched"] = bool(job["description"])
    if not job["descriptionFetched"]:
        job["descriptionFetchStatus"] = "unavailable"

    # Ensure list fields are always lists, not None
    for list_field in ("matchedKeywords", "restrictedStates"):
        if not isinstance(job.get(list_field), list):
            job[list_field] = []

    # Set createdAt if not already present
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    if not job.get("createdAt"):
        job["createdAt"] = now
    job["updatedAt"] = now

    return job


def merge_state(job: dict, state: dict) -> dict:
    """Overlay manual review state onto a normalized job dict.

    state is the full job-scout-state.json dict (with a "jobs" key).
    Looks up the per-job entry by job["id"] and overlays:
      starred, userDismissed, reviewStatus, manualNotes -> directly onto job
      overrides -> stored under job["_overrides"] (NOT flattened; build script applies)
      updatedAt  -> stored under job["_stateUpdatedAt"]
    Returns a new dict with state fields applied.
    If no state entry exists for this job ID, returns job unchanged.
    """
    if not state:
        return job
    jobs_map = state.get("jobs", {}) if isinstance(state, dict) else {}
    entry = jobs_map.get(str(job.get("id", "")))
    if not entry:
        return job
    merged = dict(job)
    for field in ("starred", "userDismissed", "reviewStatus", "manualNotes"):
        if field in entry:
            merged[field] = entry[field]
    if "overrides" in entry:
        merged["_overrides"] = entry["overrides"]
    if "updatedAt" in entry:
        merged["_stateUpdatedAt"] = entry["updatedAt"]
    return merged


def load_state(path: str) -> dict:
    """Load job-scout-state.json. Returns {"jobs": {}} if file missing or invalid."""
    import json
    try:
        with open(path) as f:
            data = json.load(f)
        if not isinstance(data, dict) or "jobs" not in data:
            return {"jobs": {}}
        return data
    except (FileNotFoundError, json.JSONDecodeError):
        return {"jobs": {}}


def save_state(path: str, state: dict) -> None:
    """Write job-scout-state.json atomically using a temp file."""
    import json, os, tempfile
    dir_ = os.path.dirname(path) or "."
    with tempfile.NamedTemporaryFile("w", dir=dir_, delete=False, suffix=".tmp") as f:
        json.dump(state, f, indent=2)
        tmp = f.name
    os.replace(tmp, path)


KEYWORD_CATEGORIES = {
    "dap": ["walkme", "walk me", "digital adoption platform", "digital adoption", "dap specialist",
            "dap consultant", "dap manager", "dap program", "dap coe", "adoption engineer",
            "adoption specialist", "adoption manager", "adoption platform", "whatfix", "appcues",
            "pendo", "userpilot", "chameleon", "nexthink", "digital experience engineer",
            "copilot adoption", "copilot enablement", "copilot champion", "copilot studio",
            "microsoft copilot", "ai copilot", "technology enablement", "technology adoption",
            "digital employee experience", "employee experience technologist", "dex specialist",
            "change enablement", "salesforce enablement", "salesforce digital adoption",
            "enterprise ai enablement", "agentic workflow", "ai orchestration", "genai implementation",
            "digital transformation specialist", "digital adoption center", "user adoption",
            "platform adoption", "software adoption", "tool adoption", "workforce adoption",
            "employee adoption", "application adoption", "spekit", "userlane", "in-app guidance",
            "in app guidance", "guided workflow", "just-in-time training", "just in time training"],
    "content": ["knowledge manager", "knowledge management", "knowledge operations", "knowledge program",
                "knowledge architect", "knowledge governance", "content operations", "content program manager",
                "content strategist", "content governance", "content architect", "information architect",
                "information architecture", "help center", "support content", "ai content",
                "self-service enablement", "support deflection", "customer education",
                "learning experience platform", "lxp", "performance support", "l&d technology",
                "instructional design", "enablement content", "knowledge base", "knowledge hub",
                "technical writing", "technical writer", "technical documentation", "documentation manager"],
}

OC_CITIES = ["aliso viejo", "anaheim", "brea", "buena park", "costa mesa", "cypress", "dana point",
             "fountain valley", "fullerton", "garden grove", "huntington beach", "irvine", "la habra",
             "la palma", "laguna beach", "laguna hills", "laguna niguel", "laguna woods", "lake forest",
             "los alamitos", "mission viejo", "newport beach", "placentia", "rancho santa margarita",
             "san juan capistrano", "santa ana", "seal beach", "stanton", "tustin",
             "villa park", "westminster", "yorba linda", "orange county", "south orange county"]

CA_EXPLICIT = [
    r"exclud\w*\s+(california|the state of california|\bca\b)", r"not\s+available\s+(in\s+)?(california|\bca\b)",
    r"except\s+(california|\bca\b)", r"california\s+residents?\s+(are\s+)?(not|excluded|ineligible)",
    r"not\s+(eligible|open)\s+(to|for)\s+california", r"\bca\b\s*residents?\s+(are\s+)?(not|excluded|ineligible)",
    r"position\s+is\s+not\s+available\s+in\s+(california|\bca\b)",
    r"open\s+to\s+all\s+states?\s+except.{0,40}(california|\bca\b)", r"\((?:excluding?|except)\s*(?:california|ca)\)",
]
US_STATES = {"AL","AK","AZ","AR","CA","CO","CT","DE","FL","GA","HI","ID","IL","IN","IA","KS","KY","LA","ME",
             "MD","MA","MI","MN","MS","MO","MT","NE","NV","NH","NJ","NM","NY","NC","ND","OH","OK","OR","PA",
             "RI","SC","SD","TN","TX","UT","VT","VA","WA","WV","WI","WY","DC"}
STATE_TRIGGER = re.compile(r"(?:(?:may\s+)?only\s+be\s+hired\s+in(?:\s+the\s+following)?(?:\s+(?:states?|locations?))?|available\s+(?:in|to)(?:\s+the\s+following)?\s+(?:states?|locations?)|open\s+to\s+(?:candidates?\s+in|the\s+following\s+(?:states?|locations?))|must\s+(?:reside|live|be\s+(?:located|based))\s+in|we\s+(?:currently\s+)?(?:hire|are\s+hiring)\s+in|hir(?:e|ing|ed)\s+in(?:\s+the\s+following)?\s+(?:states?|locations?)|the\s+following\s+(?:states?|locations?)\s*[:–-]|(?:eligible|authorized|approved|supported?)\s+(?:states?|locations?)\s*[:–-]|remote\s+(?:work\s+)?(?:available\s+)?(?:in|from)\s+(?:the\s+following\s+)?(?:states?|locations?))", re.I)


def get_description(job: dict) -> str:
    """Return the best available description text from a job dict.

    LinkedIn/Apify can return the description under different field names
    depending on the scraper version. This normalizes to the first non-empty value.
    """
    value = (
        job.get("description")
        or job.get("descriptionText")
        or job.get("descriptionHtml")
        or ""
    )
    return str(value).strip()


def should_retain_description(job: dict) -> bool:
    """Return True if this job's full description must be retained."""
    score = job.get("score", 0) or 0
    try:
        score = float(score)
    except (TypeError, ValueError):
        score = 0
    return (
        score >= 7
        or bool(job.get("starred"))
        or bool(job.get("applied"))
    )


def _text(job):
    return get_description(job).lower()


def is_in_oc(location):
    loc = (location or "").lower()
    if not re.search(r"\bca\b|california", loc):
        return False
    return any(c in loc for c in OC_CITIES)


# Broader Southern California: OC + LA metro, San Diego, Inland Empire, Long Beach.
_SOCAL_NAMES = re.compile(
    r"orange county|inland empire|southern california|greater los angeles|"
    r"los angeles|san diego|long beach|riverside|san bernardino|irvine|anaheim|"
    r"santa ana|costa mesa|newport beach|huntington beach|mission viejo|aliso viejo|"
    r"lake forest|tustin|laguna|yorba linda|fullerton|garden grove|westminster|"
    r"fountain valley|pasadena|burbank|glendale|torrance|santa monica|culver city|"
    r"el segundo|carlsbad|oceanside|temecula|murrieta|corona|rancho cucamonga|"
    r"ontario|pomona|santa clarita|thousand oaks|ventura|chino|fontana", re.I)


def is_in_socal(location):
    """True if the location is in Southern California (OC + LA/SD/IE/LB metros).

    Rejects same-named cities in other states by requiring that, when a US state
    is explicitly present, it is CA (or the text says California)."""
    loc = (location or "").strip().lower()
    if not loc:
        return False
    states = [s.upper() for s in re.findall(r",\s*([a-z]{2})\b", loc)]
    if states and "CA" not in states and "california" not in loc:
        return False
    return bool(_SOCAL_NAMES.search(loc))


# LA/IE/Riverside border cities within ~25 miles of Orange County — commutable,
# and matches the radius of the OC scrape (location=Orange County, distance=25).
OC_RING_CITIES = ["long beach", "lakewood", "cerritos", "artesia", "norwalk", "la mirada",
                  "whittier", "downey", "bellflower", "hawaiian gardens", "signal hill",
                  "diamond bar", "walnut", "rowland heights", "hacienda heights",
                  "la habra heights", "chino hills", "chino", "pomona", "montclair",
                  "corona", "norco", "pico rivera", "santa fe springs", "la puente",
                  "carson", "west covina", "covina"]


def is_commutable_to_oc(location):
    """Orange County plus the ~25-mile border ring. Central LA, San Diego and the
    far Inland Empire are NOT commutable and return False."""
    if is_in_oc(location):
        return True
    loc = (location or "").strip().lower()
    if not loc:
        return False
    states = [s.upper() for s in re.findall(r",\s*([a-z]{2})\b", loc)]
    if states and "CA" not in states and "california" not in loc:
        return False
    return any(c in loc for c in OC_RING_CITIES)


def geo_fit(workplace, location):
    """Geographic rule: keep verified-remote roles ANYWHERE (remote = no
    commute), but keep hybrid/on-site/unverified roles only if commutable to the
    home metro (OC + ~25mi ring by default). A far-away 'Hybrid', or a
    Remote-unverified in a distant metro, is NOT a fit."""
    if workplace == "Remote":
        return True
    return is_commutable_to_oc(location)


_GENERIC_LOCATION = re.compile(
    r"^(united states|usa|u\.s\.?a?\.?|remote|anywhere|worldwide|"
    r"united states of america|north america)$", re.I)


def _has_named_city(location: str) -> bool:
    """True when location is a specific city/metro, not a generic country-level string.

    Returns False (treat as generic) when location is blank, matches a generic
    country/region pattern, or contains the word 'remote' anywhere — catching
    patterns like 'Remote - USA', 'US Remote', 'United States - Remote', or
    multi-city strings like 'Austin, TX; Remote - US'.
    """
    loc = (location or "").strip().rstrip(", ").strip().lower()
    if not loc:
        return False
    if re.search(r"\bremote\b", loc):
        return False
    return not _GENERIC_LOCATION.match(loc)


_ATS_SOURCES = frozenset(("greenhouse", "lever", "ashby"))

def derive_workplace(job, src=""):
    """Evidence-first. Returns Remote | Remote — unverified | Hybrid | On-site.

    ATS sources (Greenhouse/Lever/Ashby) set workplaceType directly from the
    employer's API — trusted unconditionally.

    LinkedIn's workplaceType tag is less reliable: when it says 'Remote' but the
    location is a specific city, we require the description to confirm before
    asserting verified Remote. If the description doesn't confirm, the job gets
    'Remote — unverified' and shows up in the verify-remote action queue.
    """
    fields = " ".join(str(job.get(k) or "") for k in
                      ("workplaceType", "workType", "workRemoteType", "workplace", "locationType")).lower()
    if "hybrid" in fields:
        return "Hybrid"
    if re.search(r"on-?site|in-?person|in-?office", fields) or job.get("workRemoteAllowed") is False:
        return "On-site"
    loc = (job.get("location") or "").strip().rstrip(", ").strip().lower()
    if "remote" in fields:
        # ATS workplaceType is employer-set and reliable — trust it regardless of location.
        if job.get("source") in _ATS_SOURCES:
            return "Remote"
        # LinkedIn's Remote tag on a named-city job is ambiguous; require description proof.
        if not _has_named_city(loc):
            return "Remote"
        # Fall through to description scan below.
    t = _text(job)
    remote_first = re.search(
        r"(fully|100%|completely)\s*remote"
        r"|remote[- ](first|only|role|position|eligible|opportunity)"
        r"|work from anywhere|work from home|\bwfh\b|telecommut"
        r"|this (is|role is|position is) (a )?(fully )?remote"
        r"|position is remote|role is remote"
        r"|location[:\s\-–]+remote"
        r"|remote\s*[\(\[]?\s*(us|usa|u\.s|united states)"
        r"|remote.*\bonly\b|\bonly\b.*remote",
        t, re.I)
    hybrid = re.search(r"\bhybrid\b", t) or re.search(r"\d+\s*days?\s*(per week\s*)?(in|at|on[- ]?site|in[- ]?office)", t)
    onsite = re.search(r"\bon-?site\b|\bonsite\b|must be (located|based|able to commute)|relocat", t)
    if hybrid and not remote_first:
        return "Hybrid"
    if onsite and not remote_first:
        return "On-site"
    if remote_first:
        return "Remote"
    if not loc or _GENERIC_LOCATION.match(loc):
        return "Remote"
    # Named metro, no workplace language: trust the OC (f_WT=1,3) filter for on-site;
    # for the remote search, do NOT assert remote — flag it for review.
    if src == "oc":
        return "On-site"
    return "Remote — unverified"


# --- Salary extraction -------------------------------------------------------
# LinkedIn's structured salary badge is NOT returned by the basic scraper, so a
# salary can only be recovered when it is written into the description text. Be
# conservative: ignore dollar figures that are benefits (401k match, stipends,
# bonuses, funding amounts) or implausible as pay, and return "" when nothing
# trustworthy is found — a blank cell is better than a wrong number.
_BENEFIT_CTX = re.compile(
    r"401|stipend|\bmatch|contribut|reimburs|maximum of|per diem|equity|bonus|"
    r"discount|allowance|\bfee\b|budget|revenue|raised|valuation|fund(?:ing|ed)?",
    re.I)
_SALARY_CTX = re.compile(
    r"salary|base pay|base salary|compensation|pay range|pay band|\bpay\b|annual|"
    r"annually|per year|/\s*year|/\s*yr|hourly|per hour|/\s*hour|/\s*hr|wage|"
    r"rate of pay|range (?:is|of)", re.I)
_HOURLY_CTX = re.compile(r"/\s*h(?:r|our)|per\s+hour|hourly|an hour", re.I)
_AMT_RE = re.compile(r"\$\s?(\d[\d,]*(?:\.\d+)?)\s*([kK])?")


def _amt_to_num(numstr, kflag):
    try:
        n = float(numstr.replace(",", ""))
    except (ValueError, AttributeError):
        return None
    return n * 1000 if kflag else n


def _fmt_annual(n):
    return f"${round(n/1000)}k" if n >= 1000 else f"${round(n)}"


def _salary_from_text(text):
    low = text.lower()
    amts = []  # plausible, non-benefit dollar figures
    for m in _AMT_RE.finditer(text):
        num = _amt_to_num(m.group(1), m.group(2))
        if num is None:
            continue
        s, e = m.start(), m.end()
        ctx = low[max(0, s - 45):min(len(low), e + 25)]
        if _BENEFIT_CTX.search(ctx):
            continue
        # Magnitude decides hourly vs annual — a figure >= $1,000 is never an
        # hourly wage, even if the word "hourly" floats nearby in the text.
        hourly = num < 1000 and bool(_HOURLY_CTX.search(ctx))
        if hourly:
            if not (5 <= num <= 500):
                continue
        elif not (30000 <= num <= 1000000):
            continue
        amts.append({"v": num, "s": s, "e": e, "hourly": hourly,
                     "ctx": bool(_SALARY_CTX.search(ctx)) or hourly})
    if not amts:
        return ""

    cands = []  # (is_range, has_ctx, key_value, display)
    # ranges: two plausible same-type figures joined by a short connector
    for a, b in zip(amts, amts[1:]):
        gap = text[a["e"]:b["s"]]
        if len(gap) <= 25 and a["hourly"] == b["hourly"] and re.search(r"[-–—]|\bto\b", gap):
            lo, hi = sorted([a["v"], b["v"]])
            disp = f"${lo:g}/hr–${hi:g}/hr" if a["hourly"] else f"{_fmt_annual(lo)}–{_fmt_annual(hi)}"
            cands.append((1, 1 if (a["ctx"] or b["ctx"]) else 0, hi, disp))
    # singles
    for a in amts:
        disp = f"${a['v']:g}/hr" if a["hourly"] else _fmt_annual(a["v"])
        cands.append((0, 1 if a["ctx"] else 0, a["v"], disp))

    # prefer a range; then salary context; then larger value. A lone single with
    # no salary context nearby is not trustworthy, so never return it.
    cands.sort(key=lambda c: (c[0], c[1], c[2]), reverse=True)
    for c in cands:
        if c[0] == 1 or c[1] == 1:
            return c[3]
    return ""


_INCENTIVE_PATTERNS = [
    # (label, category, regex)
    ("RSU",          "equity",  r"\bRSU\b|\brestricted stock unit"),
    ("Stock options", "equity",  r"\bstock option|\bshare option|\bequity option"),
    ("ESPP",         "equity",  r"\bESPP\b|\bemployee stock purchase"),
    ("ESOP",         "equity",  r"\bESOP\b|\bemployee stock ownership"),
    ("Equity",       "equity",  r"\bequity\b|\bstock grant|\bshare grant|\bLTI\b|\blong.term incentive"),
    ("Bonus",        "bonus",   r"\bbonus\b|\bperformance bonus|\bannual bonus|\bmerit bonus|\btarget bonus|\bincentive bonus"),
    ("STI",          "bonus",   r"\bSTI\b|\bshort.term incentive"),
    ("OTE",          "ote",     r"\bOTE\b|\bon.target earning|\bcommission"),
    ("Profit share", "profit",  r"\bprofit.shar|\bprofit share"),
    ("401k match",   "retirement", r"\b401\s*\(?\s*k\s*\)?\s*match|\bretirement match|\bpension"),
    ("Sign-on",      "signing", r"\bsign.on bonus|\bsigning bonus"),
]

import re as _re

def detect_incentives(text: str) -> list:
    """Scan job description text and return a deduplicated list of incentive labels."""
    if not text:
        return []
    t = text.lower()
    seen_cats: set = set()
    found = []
    for label, category, pattern in _INCENTIVE_PATTERNS:
        if category in seen_cats:
            continue
        if _re.search(pattern, t, _re.IGNORECASE):
            found.append(label)
            seen_cats.add(category)
    return found


def extract_salary(job):
    s = job.get("salary") or job.get("salaryInfo") or job.get("compensationRange") or job.get("salaryRange")
    if isinstance(s, str) and s.strip():
        return s.strip()
    if isinstance(s, dict):
        mn, mx = s.get("min"), s.get("max")
        if mn and mx:
            return f"${mn/1000:.0f}k–${mx/1000:.0f}k"
        if mn:
            return f"${mn/1000:.0f}k+"
    text = get_description(job)
    return _salary_from_text(text) if text else ""


def parse_salary(job: dict, base_floor_remote: int = 143000, base_floor_oc: int = 120000,
                 target_comp: int = 183000, contract_factor: float = 0.78) -> dict:
    """Parse salary from a job dict into structured numeric fields.

    Returns a dict of salary fields to merge into the canonical job object.
    Uses the job's sourceRun ("remote" or "oc") to pick the correct floor.
    """
    _null = dict(
        salaryRaw=None, salaryLow=None, salaryHigh=None, salaryType="unknown",
        hourlyRateLow=None, hourlyRateHigh=None,
        annualizedLow=None, annualizedHigh=None,
        contractAdjustedLow=None, contractAdjustedHigh=None,
        salaryPassesBaseFloor=None, salaryPassesTargetComp=None,
        compRisk="unknown",
    )

    raw = extract_salary(job) or job.get("salaryRaw") or job.get("salary") or ""
    raw = str(raw).strip()
    _null["salaryRaw"] = raw or None
    if not raw:
        return _null

    low_raw = raw.lower()

    # --- Detect salary type ---
    if re.search(r"/\s*h(?:r|our)|per\s+hour|hourly|an\s+hour", low_raw):
        sal_type = "hourly"
    elif re.search(r"/\s*y(?:r|ear)|per\s+year|annual(?:ly)?|\bsalary\b", low_raw):
        sal_type = "annual"
    else:
        sal_type = "unknown"

    # --- Extract numeric values (handles $143,000, $143K, 143000, 143k) ---
    _NUM_RE = re.compile(r"\$?\s*(\d[\d,]*(?:\.\d+)?)\s*([kK])?")
    nums = []
    for m in _NUM_RE.finditer(raw):
        try:
            n = float(m.group(1).replace(",", ""))
        except ValueError:
            continue
        if m.group(2):
            n *= 1000
        nums.append(n)

    # Filter to plausible magnitudes based on type
    if sal_type == "hourly":
        nums = [n for n in nums if 5 <= n <= 500]
    elif sal_type == "annual":
        nums = [n for n in nums if 10000 <= n <= 2000000]
    else:
        # Try annual first, then hourly
        annual_nums = [n for n in nums if 10000 <= n <= 2000000]
        hourly_nums = [n for n in nums if 5 <= n <= 500]
        if annual_nums:
            nums = annual_nums
            sal_type = "annual"
        elif hourly_nums:
            nums = hourly_nums
            sal_type = "hourly"
        else:
            nums = []

    if not nums:
        return dict(_null, salaryRaw=raw or None)

    sal_low_raw = min(nums)
    sal_high_raw = max(nums)

    # --- Annualize hourly ---
    if sal_type == "hourly":
        result = dict(_null)
        result["salaryRaw"] = raw
        result["salaryType"] = "hourly"
        result["hourlyRateLow"] = sal_low_raw
        result["hourlyRateHigh"] = sal_high_raw
        ann_low = sal_low_raw * 2080
        ann_high = sal_high_raw * 2080
        result["annualizedLow"] = ann_low
        result["annualizedHigh"] = ann_high
        result["contractAdjustedLow"] = round(ann_low * contract_factor)
        result["contractAdjustedHigh"] = round(ann_high * contract_factor)
        result["salaryLow"] = None
        result["salaryHigh"] = None
        result["compRisk"] = "contract-verify"
        result["salaryPassesBaseFloor"] = None
        result["salaryPassesTargetComp"] = None
        return result

    # --- Annual salary ---
    floor = base_floor_oc if (job.get("sourceRun") or "").lower() == "oc" else base_floor_remote

    result = dict(_null)
    result["salaryRaw"] = raw
    result["salaryType"] = "annual"
    result["salaryLow"] = sal_low_raw
    result["salaryHigh"] = sal_high_raw
    result["salaryPassesBaseFloor"] = sal_high_raw >= floor
    result["salaryPassesTargetComp"] = sal_high_raw >= target_comp

    if sal_high_raw < floor:
        result["compRisk"] = "below-floor"
    elif sal_low_raw < floor <= sal_high_raw:
        result["compRisk"] = "top-of-range-only"
    else:
        result["compRisk"] = "none"

    return result


def parse_geo_eligibility(job: dict) -> dict:
    """Detect remote eligibility and CA eligibility from job description and location.

    Returns a dict of geo fields to merge into the canonical job object.
    """
    result = {
        "remoteEligibility": None,
        "restrictedStates": [],
        "caEligible": None,
        "caExcluded": False,
        "geoFit": None,
        "geoFitReason": None,
    }

    desc = get_description(job)
    low = desc.lower()
    workplace = job.get("workplaceNormalized") or ""

    ca_excluded = False
    ca_eligible = None
    remote_eligibility = None
    restricted_states = []

    # --- Explicit CA-positive signals ---
    if re.search(r"open\s+to\s+candidates?\s+in\s+california|ca\s+eligible|california\s+eligible|"
                 r"available\s+(nationwide|everywhere|us[- ]wide|across\s+the\s+us|anywhere\s+in\s+the\s+us)",
                 low):
        ca_eligible = True
        remote_eligibility = "US-wide"

    if re.search(r"available\s+nationwide|us[- ]wide|anywhere\s+in\s+the\s+us|"
                 r"all\s+50\s+states|nationwide\s+remote|work\s+from\s+anywhere", low):
        ca_eligible = True
        remote_eligibility = "US-wide"

    # --- Explicit CA-exclusion signals ---
    if not ca_excluded and any(re.search(p, low) for p in CA_EXPLICIT):
        ca_excluded = True
        ca_eligible = False
        remote_eligibility = remote_eligibility or "state restricted"

    if not ca_excluded and re.search(r"not\s+eligible\s+in\s+california|not\s+available\s+in\s+california", low):
        ca_excluded = True
        ca_eligible = False
        remote_eligibility = remote_eligibility or "state restricted"

    # --- "not available in [states]" pattern ---
    _not_avail = re.search(
        r"not\s+available\s+in\s+((?:[a-z\s,]+(?:\bor\b|\band\b)?)+)", low)
    if _not_avail and not ca_excluded:
        snippet = _not_avail.group(1)
        if re.search(r"\bcalifornia\b|\bca\b", snippet):
            ca_excluded = True
            ca_eligible = False

    # --- "only available in [states]" / "hiring in [states] only" / "must reside in [states]" ---
    _only_pat = re.search(
        r"(?:only\s+available\s+in|hiring\s+in(?:\s+the\s+following)?\s+(?:states?\s+)?only|"
        r"must\s+(?:reside|live|be\s+(?:located|based))\s+in)\s+((?:[a-z\s,]+(?:\bor\b|\band\b)?)+)",
        low)
    if _only_pat and ca_eligible is not True:
        snippet = _only_pat.group(1)
        if not re.search(r"\bcalifornia\b|\bca\b", snippet):
            ca_eligible = False
            remote_eligibility = remote_eligibility or "state restricted"
            # Extract 2-letter state codes from original case text
            pos = _only_pat.start(1)
            orig_snippet = desc[pos: pos + len(snippet)]
            states_found = [s for s in re.findall(r"\b([A-Z]{2})\b", orig_snippet) if s in US_STATES]
            if states_found:
                restricted_states = states_found

    # --- "remote from approved states" ---
    if re.search(r"remote\s+from\s+(?:approved|select|certain|specific)\s+states?", low):
        remote_eligibility = remote_eligibility or "state restricted"
        if ca_eligible is None:
            ca_eligible = None  # genuinely unknown

    # --- State trigger list without CA (existing logic) ---
    if not ca_excluded and _state_list_without_ca(desc):
        ca_eligible = False
        remote_eligibility = remote_eligibility or "state restricted"

    # --- Workplace-based defaults ---
    if workplace == "Remote":
        if remote_eligibility is None:
            remote_eligibility = "US-wide"
        if ca_eligible is None and not ca_excluded:
            ca_eligible = True
    elif workplace == "Remote — unverified":
        if remote_eligibility is None:
            remote_eligibility = "unknown"
        # ca_eligible stays None (unknown)

    # --- Commute for hybrid/on-site ---
    commute_minutes = job.get("commuteEstimateMinutes")
    commute_unknown = job.get("commuteUnknown", True)
    if commute_minutes is None and workplace not in ("Remote", "Remote — unverified"):
        loc = job.get("locationRaw") or job.get("location") or ""
        commute_info = get_commute_estimate(loc)
        commute_minutes = commute_info.get("estimatedMinutes")
        commute_unknown = commute_info.get("commuteUnknown", True)

    # --- geoFit logic ---
    geo_fit_val = None
    geo_fit_reason = None

    if ca_excluded:
        geo_fit_val = False
        geo_fit_reason = "CA excluded"
    elif workplace in ("Remote", "Remote — unverified"):
        if ca_eligible is False:
            geo_fit_val = False
            geo_fit_reason = "remote but CA not eligible"
        elif ca_eligible is True:
            geo_fit_val = True
            geo_fit_reason = "remote, CA eligible"
        else:
            # Remote — unverified or unknown CA status
            geo_fit_val = None
            geo_fit_reason = "remote eligibility unverified"
    elif workplace in ("Hybrid", "On-site"):
        if commute_unknown:
            geo_fit_val = None
            geo_fit_reason = "hybrid/on-site, commute unknown"
        elif commute_minutes is not None and commute_minutes <= 40:
            geo_fit_val = True
            geo_fit_reason = f"hybrid/on-site, ~{commute_minutes} min commute"
        elif commute_minutes is not None and commute_minutes > 45:
            geo_fit_val = False
            geo_fit_reason = f"hybrid/on-site, ~{commute_minutes} min commute (too far)"
        else:
            geo_fit_val = None
            geo_fit_reason = f"hybrid/on-site, borderline commute (~{commute_minutes} min)"
    else:
        geo_fit_val = None
        geo_fit_reason = "workplace type unknown"

    result["remoteEligibility"] = remote_eligibility
    result["restrictedStates"] = restricted_states
    result["caEligible"] = ca_eligible
    result["caExcluded"] = ca_excluded
    result["geoFit"] = geo_fit_val
    result["geoFitReason"] = geo_fit_reason
    return result


def get_commute_estimate(location_str: str) -> dict:
    """Look up estimated commute time from the configured home city for a city name.

    Commute origin comes from geo.homeCity in config/jobscout_config.json (the
    commute_map.json times are precomputed relative to that origin).

    Returns {"estimatedMinutes": N, "fit": True/False/"borderline", "commuteUnknown": False}
    or {"estimatedMinutes": None, "fit": None, "commuteUnknown": True} if city not found.
    """
    import json, os
    map_path = os.path.join(os.path.dirname(__file__), "commute_map.json")
    try:
        with open(map_path) as f:
            commute_map = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {"estimatedMinutes": None, "fit": None, "commuteUnknown": True}

    if not location_str:
        return {"estimatedMinutes": None, "fit": None, "commuteUnknown": True}

    # Try to match city name (case-insensitive, partial match)
    loc_lower = location_str.lower()
    for city, data in commute_map.items():
        if city.lower() in loc_lower or loc_lower in city.lower():
            return {
                "estimatedMinutes": data["estimatedMinutes"],
                "fit": data["fit"],
                "commuteUnknown": False,
            }

    return {"estimatedMinutes": None, "fit": None, "commuteUnknown": True}


def _state_list_without_ca(text):
    m = STATE_TRIGGER.search(text)
    if not m:
        return False
    excerpt = text[m.end():m.end() + 500]
    found = [x for x in re.findall(r"\b([A-Z]{2})\b", excerpt) if x in US_STATES]
    # An explicit allowlist ("only be hired in...") is trusted even when short;
    # weaker triggers still need >=4 state codes to avoid incidental mentions.
    context = text[max(0, m.start() - 40):m.end()].lower()
    min_states = 1 if "only" in context else 4
    return len(found) >= min_states and "CA" not in found


def is_ca_excluded(job):
    raw = get_description(job)
    low = raw.lower()
    if any(re.search(p, low) for p in CA_EXPLICIT):
        return True
    return _state_list_without_ca(raw)


def matched_keywords(job):
    text = ((job.get("title") or "") + " " + get_description(job) + " " + (job.get("companyName") or "")).lower()
    dap = [k for k in KEYWORD_CATEGORIES["dap"] if k in text]
    content = [k for k in KEYWORD_CATEGORIES["content"] if k in text]
    return dap[:6], content[:6]


def job_category(dap, content):
    if dap and content:
        return "DAP + Content"
    if dap:
        return "DAP"
    if content:
        return "Content"
    return ""


# The 20 search-keyword phrases used by the scraper (STEP 3/4). Provenance here is
# APPROXIMATE: the combined scrape doesn't tag which keyword URL surfaced a job, so
# we infer it by matching these phrases against the title + description. Good enough
# to spot consistently low-quality keywords for pruning.
SEARCH_KEYWORDS = [
    "knowledge manager", "knowledge operations", "content operations manager",
    "content program manager", "knowledge management specialist", "support content strategist",
    "ai knowledge management", "content governance manager", "kcs", "documentation manager",
    "knowledge program manager", "technical writing manager", "support content manager",
    "knowledge strategist", "information architect", "self-service content manager",
    "knowledge base manager", "digital adoption", "digital employee experience",
    "product adoption", "user adoption",
]


def search_keyword_matches(job):
    """Approximate keyword provenance: which search phrases appear in title+desc."""
    text = ((job.get("title") or "") + " \n " + get_description(job)).lower()
    out = []
    for kw in SEARCH_KEYWORDS:
        if kw == "kcs":
            if re.search(r"\bkcs\b", text):
                out.append(kw)
        elif kw in text:
            out.append(kw)
    return out


def classify(job, src=""):
    """Return all derived fields for a scraped job."""
    dap, content = matched_keywords(job)
    oc = is_in_oc(job.get("location"))
    workplace = derive_workplace(job, "oc" if (src == "oc" or oc) else (src or "remote"))
    return {
        "workplace": workplace,
        "salary": extract_salary(job),
        "caExcluded": is_ca_excluded(job),
        "keywords": sorted(set(dap + content)),
        "category": job_category(dap, content),
        "ocTarget": oc,
        "geoFit": geo_fit(workplace, job.get("location")),
    }
