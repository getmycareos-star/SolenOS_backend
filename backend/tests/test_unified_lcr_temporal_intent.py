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


def test_relative_date_resolution_uses_injected_reference_time_and_timezone():
    from datetime import datetime
    from zoneinfo import ZoneInfo

    from app.services.unified_lcr_retrieval import _temporal_window

    reference = datetime(2026, 11, 26, 1, 30, tzinfo=ZoneInfo("UTC"))
    window = _temporal_window(
        "What happened yesterday?",
        now=reference,
        timezone_name="America/Los_Angeles",
    )

    assert window is not None
    assert window["timezone"] == "America/Los_Angeles"
    # At 01:30 UTC it is still November 25 in Los Angeles.
    assert window["basis"] == "explicit_user_temporal_expression"
    assert window["start"].startswith("2026-11-24T08:00:00")
    assert window["end"].startswith("2026-11-25T07:59:59")


def test_relative_date_resolution_does_not_depend_on_wall_clock_when_injected():
    from datetime import datetime, timezone

    from app.services.unified_lcr_retrieval import _temporal_window

    reference = datetime(2024, 3, 1, 12, 0, tzinfo=timezone.utc)
    window = _temporal_window(
        "Show me the last week",
        now=reference,
        timezone_name="UTC",
    )

    assert window is not None
    assert window["start"].startswith("2024-02-19T00:00:00")
    assert window["end"].startswith("2024-02-25T23:59:59")
