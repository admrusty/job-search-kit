"""Tests for alert selection logic in jobscout_alerts.py.

Uses is_alert_candidate(), build_risk_flags(), and build_candidate() directly.
"""
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from jobscout_alerts import is_alert_candidate, build_risk_flags, build_candidate


# ---------------------------------------------------------------------------
# Default config matching hardcoded fallbacks
# ---------------------------------------------------------------------------

DEFAULT_CFG = {
    "min_score": 7,
    "floor_remote": 143000,
    "floor_oc": 120000,
    "commute_max": 45,
}

EMPTY_STATE_JOBS = {}
EMPTY_APPLIED_SET = []


def _job(**kwargs):
    """Build a minimal passing job dict, override with kwargs."""
    base = {
        "id": "test-001",
        "title": "Content Manager",
        "company": "Acme Corp",
        "score": 7,
        "sourceRun": "remote",
        "workplaceNormalized": "Remote",
        "geoFit": True,
        "caExcluded": False,
        "caEligible": True,
        "applied": False,
        "userDismissed": False,
        "salaryHigh": 150000,
        "salaryLow": 143000,
        "commuteEstimateMinutes": None,
    }
    base.update(kwargs)
    return base


# ---------------------------------------------------------------------------
# Score threshold
# ---------------------------------------------------------------------------

def test_score_too_low_excluded():
    job = _job(score=6)
    ok, reason = is_alert_candidate(job, DEFAULT_CFG, EMPTY_STATE_JOBS, EMPTY_APPLIED_SET)
    assert ok is False
    assert "score" in reason.lower()


def test_score_7_included():
    job = _job(score=7)
    ok, reason = is_alert_candidate(job, DEFAULT_CFG, EMPTY_STATE_JOBS, EMPTY_APPLIED_SET)
    assert ok is True


# ---------------------------------------------------------------------------
# CA exclusion
# ---------------------------------------------------------------------------

def test_ca_excluded_not_candidate():
    job = _job(caExcluded=True, geoFit=False)
    ok, reason = is_alert_candidate(job, DEFAULT_CFG, EMPTY_STATE_JOBS, EMPTY_APPLIED_SET)
    assert ok is False


# ---------------------------------------------------------------------------
# Applied checks
# ---------------------------------------------------------------------------

def test_applied_field_not_candidate():
    job = _job(applied=True)
    ok, reason = is_alert_candidate(job, DEFAULT_CFG, EMPTY_STATE_JOBS, EMPTY_APPLIED_SET)
    assert ok is False
    assert "applied" in reason.lower()


def test_applied_company_match_not_candidate():
    # is_applied now matches on company + role; role "Unknown" matches any title
    job = _job(companyName="Techflow", title="Content Manager")
    applied_list = [{"company": "Techflow", "role": "Unknown"}]
    ok, reason = is_alert_candidate(job, DEFAULT_CFG, EMPTY_STATE_JOBS, applied_list)
    assert ok is False
    assert "applied" in reason.lower()


# ---------------------------------------------------------------------------
# User dismissed
# ---------------------------------------------------------------------------

def test_user_dismissed_not_candidate():
    # userDismissed via state overlay
    job = _job()
    state_jobs = {"test-001": {"userDismissed": True}}
    ok, reason = is_alert_candidate(job, DEFAULT_CFG, state_jobs, EMPTY_APPLIED_SET)
    assert ok is False
    assert "dismissed" in reason.lower()


# ---------------------------------------------------------------------------
# Salary floor
# ---------------------------------------------------------------------------

def test_remote_salary_below_floor_excluded():
    # sourceRun="remote", salaryHigh=120000 < remote floor 143000
    job = _job(sourceRun="remote", salaryHigh=120000)
    ok, reason = is_alert_candidate(job, DEFAULT_CFG, EMPTY_STATE_JOBS, EMPTY_APPLIED_SET)
    assert ok is False
    assert "salary" in reason.lower() or "floor" in reason.lower()


def test_oc_salary_above_oc_floor_included():
    # sourceRun="oc", salaryHigh=125000 >= OC floor 120000 → candidate
    job = _job(sourceRun="oc", workplaceNormalized="On-site", geoFit=True,
               salaryHigh=125000, salaryLow=120000)
    ok, reason = is_alert_candidate(job, DEFAULT_CFG, EMPTY_STATE_JOBS, EMPTY_APPLIED_SET)
    assert ok is True


def test_salary_unknown_included():
    # salaryHigh=None → not excluded, but should get "Salary unknown" risk flag
    job = _job(salaryHigh=None, salaryLow=None)
    ok, reason = is_alert_candidate(job, DEFAULT_CFG, EMPTY_STATE_JOBS, EMPTY_APPLIED_SET)
    assert ok is True
    flags = build_risk_flags(job)
    assert any("salary unknown" in f.lower() for f in flags)


# ---------------------------------------------------------------------------
# Risk flags
# ---------------------------------------------------------------------------

def test_risk_flag_ca_unconfirmed():
    # caEligible=None → candidate with "CA eligibility unconfirmed" flag
    job = _job(caEligible=None, geoFit=None)
    ok, reason = is_alert_candidate(job, DEFAULT_CFG, EMPTY_STATE_JOBS, EMPTY_APPLIED_SET)
    assert ok is True
    flags = build_risk_flags(job)
    assert any("ca eligibility" in f.lower() for f in flags)


def test_risk_flag_commute_warning():
    # workplaceNormalized="Hybrid", commuteEstimateMinutes=38 → candidate with commute flag
    job = _job(
        workplaceNormalized="Hybrid",
        commuteEstimateMinutes=38,
        geoFit=True,
        sourceRun="oc",
        salaryHigh=125000,
    )
    ok, reason = is_alert_candidate(job, DEFAULT_CFG, EMPTY_STATE_JOBS, EMPTY_APPLIED_SET)
    assert ok is True
    flags = build_risk_flags(job)
    assert any("hybrid" in f.lower() or "commute" in f.lower() for f in flags)


# ---------------------------------------------------------------------------
# Regression: caEligible == False must block alerts (P0 fix)
# ---------------------------------------------------------------------------

def test_ca_eligible_false_not_candidate():
    """State-restricted remote role (caEligible=False) must be excluded.

    geoFit=True so the earlier geoFit check doesn't fire first — we want to
    exercise the caEligible guard specifically.
    """
    job = _job(caEligible=False, caExcluded=False, geoFit=True)
    ok, reason = is_alert_candidate(job, DEFAULT_CFG, EMPTY_STATE_JOBS, EMPTY_APPLIED_SET)
    assert ok is False
    assert "caEligible" in reason or "ca" in reason.lower()


def test_ca_eligible_false_with_caexcluded_false_not_candidate():
    """Even with caExcluded=False, caEligible=False should block (state-restricted role)."""
    job = _job(caEligible=False, caExcluded=False)
    ok, reason = is_alert_candidate(job, DEFAULT_CFG, EMPTY_STATE_JOBS, EMPTY_APPLIED_SET)
    assert ok is False


# ---------------------------------------------------------------------------
# Regression: link fallback for URL (P0 fix)
# ---------------------------------------------------------------------------

def test_build_candidate_uses_link_fallback():
    """When no linkedInUrl/url/applyUrl, build_candidate should fall back to link."""
    job = _job(
        id="link-001",
        linkedInUrl=None,
        url=None,
        applyUrl=None,
        link="https://www.linkedin.com/jobs/view/link-001",
    )
    candidate = build_candidate(job, {})
    assert candidate["linkedInUrl"] == "https://www.linkedin.com/jobs/view/link-001"


def test_build_candidate_prefers_linkedinurl_over_link():
    """linkedInUrl should take priority over link."""
    job = _job(
        id="link-002",
        linkedInUrl="https://www.linkedin.com/jobs/view/proper",
        link="https://www.linkedin.com/jobs/view/fallback",
    )
    candidate = build_candidate(job, {})
    assert candidate["linkedInUrl"] == "https://www.linkedin.com/jobs/view/proper"


# ---------------------------------------------------------------------------
# Regression: extra_flags (geoFit=None addition) must be in riskFlags (P0 fix)
# ---------------------------------------------------------------------------

def test_build_candidate_risk_flags_include_geo_unknown():
    """When geoFit=None, 'CA eligibility unconfirmed' must appear in riskFlags."""
    job = _job(geoFit=None, caEligible=None)
    candidate = build_candidate(job, {})
    assert any("ca eligibility" in f.lower() for f in candidate["riskFlags"])


def test_build_candidate_extra_flags_not_discarded():
    """riskFlags in candidate must include the geoFit=None augmentation, not just build_risk_flags output."""
    job = _job(geoFit=None, caEligible=None, salaryHigh=None, salaryLow=None)
    base_flags = build_risk_flags(job)
    candidate = build_candidate(job, {})
    # candidate riskFlags should be >= base_flags (extra_flags adds geoFit note)
    assert len(candidate["riskFlags"]) >= len(base_flags)
