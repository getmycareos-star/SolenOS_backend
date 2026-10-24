from datetime import timezone

import pytest
from fastapi import HTTPException

from app.api.change import _parse_reference_time


def test_reference_time_accepts_utc_z_suffix():
    value = _parse_reference_time("2026-10-22T17:00:00Z")
    assert value is not None
    assert value.year == 2026
    assert value.tzinfo == timezone.utc


def test_reference_time_normalizes_legacy_naive_timestamp_to_utc():
    value = _parse_reference_time("2026-10-22T17:00:00")
    assert value is not None
    assert value.tzinfo == timezone.utc


def test_reference_time_preserves_explicit_offset():
    value = _parse_reference_time("2026-10-22T17:00:00+02:00")
    assert value is not None
    assert value.utcoffset().total_seconds() == 7200


def test_reference_time_rejects_invalid_value_as_client_error():
    with pytest.raises(HTTPException) as exc:
        _parse_reference_time("tomorrow afternoon")
    assert exc.value.status_code == 422
    assert "ISO-8601" in exc.value.detail


def test_reference_time_absent_is_none():
    assert _parse_reference_time(None) is None
    assert _parse_reference_time("") is None
