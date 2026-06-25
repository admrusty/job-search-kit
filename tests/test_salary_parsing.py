"""Tests for parse_salary() in jobscout_core."""
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from jobscout_core import parse_salary


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _job_with_salary(salary_str, source_run="remote"):
    """Minimal job dict with salary embedded in description (for text extraction)."""
    return {
        "id": "test-001",
        "sourceRun": source_run,
        # Put salary in description so extract_salary can find it via _salary_from_text
        "description": f"The salary range is {salary_str} per year.",
    }


def _job_with_salary_field(salary_str, source_run="remote"):
    """Job with an explicit 'salary' field (bypasses text extraction)."""
    return {
        "id": "test-001",
        "sourceRun": source_run,
        "salary": salary_str,
    }


# ---------------------------------------------------------------------------
# Basic range parsing
# ---------------------------------------------------------------------------

def test_annual_range_standard_format():
    job = _job_with_salary_field("$143,000 - $190,000")
    result = parse_salary(job)
    assert result["salaryLow"] == 143000
    assert result["salaryHigh"] == 190000
    assert result["compRisk"] == "none"
    assert result["salaryType"] == "annual"


def test_annual_range_k_suffix():
    job = _job_with_salary_field("$143K - $190K")
    result = parse_salary(job)
    assert result["salaryLow"] == 143000
    assert result["salaryHigh"] == 190000
    assert result["compRisk"] == "none"


def test_single_value():
    # A single annual value with salary context in description
    job = {
        "id": "test-001",
        "sourceRun": "remote",
        "description": "Base salary: $150,000 annually.",
    }
    result = parse_salary(job)
    assert result["salaryHigh"] == 150000


def test_empty_salary_returns_unknown():
    # Job with no salary fields at all
    job = {"id": "test-001", "sourceRun": "remote"}
    result = parse_salary(job)
    assert result["compRisk"] == "unknown"
    assert result["salaryHigh"] is None


def test_no_salary_field_returns_unknown():
    # Job with explicit empty description and no salary fields
    job = {"id": "test-001", "sourceRun": "remote", "description": "Great opportunity!"}
    result = parse_salary(job)
    assert result["compRisk"] == "unknown"


# ---------------------------------------------------------------------------
# Floor checks
# ---------------------------------------------------------------------------

def test_salary_passes_floor():
    # high=$190K > remote floor=$143K
    job = _job_with_salary_field("$143,000 - $190,000", source_run="remote")
    result = parse_salary(job)
    assert result["salaryPassesBaseFloor"] is True


def test_salary_fails_floor():
    # high=$110K < remote floor=$143K
    job = _job_with_salary_field("$90,000 - $110,000", source_run="remote")
    result = parse_salary(job)
    assert result["compRisk"] == "below-floor"
    assert result["salaryPassesBaseFloor"] is False


def test_top_of_range_only():
    # low=$100K < floor=$143K, high=$145K >= floor → top-of-range-only
    job = _job_with_salary_field("$100,000 - $145,000", source_run="remote")
    result = parse_salary(job)
    assert result["compRisk"] == "top-of-range-only"
    assert result["salaryPassesBaseFloor"] is True


def test_oc_uses_lower_floor():
    # $125K >= OC floor ($120K) → passes
    job = _job_with_salary_field("$120,000 - $125,000", source_run="oc")
    result = parse_salary(job, base_floor_oc=120000)
    assert result["salaryPassesBaseFloor"] is True


def test_remote_uses_higher_floor():
    # $125K < remote floor ($143K) → fails
    job = _job_with_salary_field("$120,000 - $125,000", source_run="remote")
    result = parse_salary(job, base_floor_remote=143000)
    assert result["salaryPassesBaseFloor"] is False


# ---------------------------------------------------------------------------
# Hourly
# ---------------------------------------------------------------------------

def test_hourly_rate_annualizes():
    # $75/hr → annualizedHigh = 75 * 2080 = 156000, compRisk="contract-verify"
    job = _job_with_salary_field("$75/hr")
    result = parse_salary(job)
    assert result["salaryType"] == "hourly"
    assert result["annualizedHigh"] == 75 * 2080
    assert result["compRisk"] == "contract-verify"
    assert result["hourlyRateHigh"] == 75


def test_hourly_rate_below_annualized_floor():
    # $50/hr → annualized=104000; still compRisk="contract-verify" (hourly always is)
    job = _job_with_salary_field("$50/hr")
    result = parse_salary(job)
    assert result["salaryType"] == "hourly"
    assert result["annualizedHigh"] == 50 * 2080
    assert result["compRisk"] == "contract-verify"


# ---------------------------------------------------------------------------
# Target comp
# ---------------------------------------------------------------------------

def test_salary_passes_target_comp():
    # high=$190K >= target=$183K
    job = _job_with_salary_field("$143,000 - $190,000", source_run="remote")
    result = parse_salary(job, target_comp=183000)
    assert result["salaryPassesTargetComp"] is True


def test_salary_fails_target_comp():
    # high=$150K < target=$183K
    job = _job_with_salary_field("$143,000 - $150,000", source_run="remote")
    result = parse_salary(job, target_comp=183000)
    assert result["salaryPassesTargetComp"] is False
