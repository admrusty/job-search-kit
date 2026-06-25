"""Tests for get_description() in jobscout_core."""
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from jobscout_core import get_description


def test_prefers_description_field():
    job = {"description": "This is the main description."}
    assert get_description(job) == "This is the main description."


def test_falls_back_to_descriptionText():
    job = {"descriptionText": "Text field description."}
    assert get_description(job) == "Text field description."


def test_falls_back_to_descriptionHtml():
    job = {"descriptionHtml": "<p>HTML description.</p>"}
    assert get_description(job) == "<p>HTML description.</p>"


def test_returns_empty_string_when_all_missing():
    job = {"title": "Some Job", "company": "Acme"}
    assert get_description(job) == ""


def test_returns_empty_string_when_all_none():
    job = {"description": None, "descriptionText": None, "descriptionHtml": None}
    assert get_description(job) == ""


def test_strips_whitespace():
    job = {"description": "  Leading and trailing spaces.  "}
    assert get_description(job) == "Leading and trailing spaces."


def test_prefers_description_over_descriptionText_when_both_present():
    job = {
        "description": "Primary description.",
        "descriptionText": "Fallback description.",
    }
    assert get_description(job) == "Primary description."
