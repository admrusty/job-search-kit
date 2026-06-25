"""Shared fixtures for Job Scout tests."""
import pytest


@pytest.fixture
def minimal_job():
    """A minimal valid job dict with no salary or geo fields set."""
    return {
        "id": "test-001",
        "title": "Content Manager",
        "company": "Acme Corp",
        "sourceRun": "remote",
        "workplaceNormalized": "Remote",
        "locationRaw": "United States",
    }


@pytest.fixture
def default_cfg():
    """Default alert config matching hardcoded fallbacks in jobscout_alerts.py."""
    return {
        "min_score": 7,
        "floor_remote": 143000,
        "floor_oc": 120000,
        "commute_max": 45,
    }
