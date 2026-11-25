"""Temporal intent tests: date-bearing care reports must not be treated as lookups."""

from app.services.unified_lcr_retrieval import _is_temporal_lookup_request


def test_relative_dates_in_caregiver_statements_do_not_require_timezone():
    assert not _is_temporal_lookup_request("She did not finish lunch yesterday.")
    assert not _is_temporal_lookup_request("Her sleep has been worse since last Friday.")
    assert not _is_temporal_lookup_request("Last Friday she refused lunch.")


def test_relative_dates_in_history_questions_and_requests_are_lookups():
    assert _is_temporal_lookup_request("What happened yesterday?")
    assert _is_temporal_lookup_request("Show me changes since last Friday.")
    assert _is_temporal_lookup_request("I want to know what changed last week.")
    assert _is_temporal_lookup_request("Can you compare her sleep this month?")
