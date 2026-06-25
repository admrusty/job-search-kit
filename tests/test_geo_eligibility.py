"""Tests for parse_geo_eligibility() in jobscout_core."""
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from jobscout_core import parse_geo_eligibility


# ---------------------------------------------------------------------------
# Remote / CA eligibility
# ---------------------------------------------------------------------------

def test_remote_us_wide_no_restrictions():
    # Remote job, no state restrictions → caEligible=True, geoFit=True
    job = {
        "workplaceNormalized": "Remote",
        "description": "This is a fully remote role open to all US candidates.",
    }
    result = parse_geo_eligibility(job)
    assert result["caEligible"] is True
    assert result["geoFit"] is True


def test_ca_explicitly_excluded():
    # description contains "not available in California"
    job = {
        "workplaceNormalized": "Remote",
        "description": "This position is not available in California.",
    }
    result = parse_geo_eligibility(job)
    assert result["caExcluded"] is True
    assert result["caEligible"] is False
    assert result["geoFit"] is False


def test_state_restricted_excludes_ca():
    # "must reside in NC, NY, or TX" — CA not in list → caEligible=False
    job = {
        "workplaceNormalized": "Remote",
        "description": "Candidates must reside in NC, NY, or TX.",
    }
    result = parse_geo_eligibility(job)
    assert result["caEligible"] is False
    assert result["geoFit"] is False


def test_state_restricted_includes_ca():
    # "available in CA, NY, TX" → caEligible=True
    job = {
        "workplaceNormalized": "Remote",
        "description": "This role is only available in CA, NY, TX.",
    }
    result = parse_geo_eligibility(job)
    assert result["caEligible"] is True
    assert result["geoFit"] is True


def test_approved_states_unknown():
    # "remote from approved states" → remoteEligibility="state restricted", caEligible=None
    # Must use "Remote — unverified" workplace so the Remote default doesn't override caEligible to True
    job = {
        "workplaceNormalized": "Remote — unverified",
        "description": "This position is remote from approved states only.",
    }
    result = parse_geo_eligibility(job)
    assert result["remoteEligibility"] == "state restricted"
    assert result["caEligible"] is None


def test_us_wide_explicit():
    # "available nationwide" → caEligible=True, remoteEligibility="US-wide"
    job = {
        "workplaceNormalized": "Remote",
        "description": "This role is available nationwide.",
    }
    result = parse_geo_eligibility(job)
    assert result["caEligible"] is True
    assert result["remoteEligibility"] == "US-wide"


# ---------------------------------------------------------------------------
# On-site / commute-based geo fit
# The commute_map.json has:
#   Irvine → 35 min (fit=True, commuteUnknown=False)
#   Los Angeles → 90 min (fit=False, commuteUnknown=False)
# parse_geo_eligibility calls get_commute_estimate when workplace is not Remote/unverified.
# geoFit logic: <=40 → True, >45 → False, 41-45 → None (borderline)
# ---------------------------------------------------------------------------

def test_onsite_oc_commute_fit():
    # Irvine, CA → 35 min commute → geoFit=True
    job = {
        "workplaceNormalized": "On-site",
        "locationRaw": "Irvine, CA",
        "description": "",
    }
    result = parse_geo_eligibility(job)
    assert result["geoFit"] is True


def test_onsite_la_commute_not_fit():
    # Los Angeles, CA → 90 min commute → geoFit=False
    job = {
        "workplaceNormalized": "On-site",
        "locationRaw": "Los Angeles, CA",
        "description": "",
    }
    result = parse_geo_eligibility(job)
    assert result["geoFit"] is False


# ---------------------------------------------------------------------------
# Remote — unverified
# ---------------------------------------------------------------------------

def test_remote_unverified_ca_unknown():
    # "Remote — unverified" → caEligible stays None (not False)
    job = {
        "workplaceNormalized": "Remote — unverified",
        "description": "Remote opportunity for the right candidate.",
    }
    result = parse_geo_eligibility(job)
    assert result["caEligible"] is None
