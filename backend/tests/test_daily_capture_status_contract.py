"""Shared extraction-state contract for Daily Care history and retry receipts."""

from types import SimpleNamespace

import pytest

from app.services.daily_capture_status import daily_capture_extraction_status


@pytest.mark.parametrize(
    ("job_status", "expected"),
    [
        ("queued", "queued"),
        ("processing", "queued"),
        ("failed", "failed"),
        ("cancelled", "failed"),
        ("completed", "unavailable"),
        ("unknown", "unavailable"),
    ],
)
def test_job_state_without_persisted_analysis_never_claims_extraction_success(job_status, expected):
    job = SimpleNamespace(status=job_status)
    assert daily_capture_extraction_status(None, job) == expected


def test_persisted_analysis_is_the_authoritative_extraction_status():
    analysis = SimpleNamespace(extraction_status="available")
    completed_job = SimpleNamespace(status="completed")
    assert daily_capture_extraction_status(analysis, completed_job) == "available"


def test_missing_job_and_analysis_is_unavailable():
    assert daily_capture_extraction_status(None, None) == "unavailable"
