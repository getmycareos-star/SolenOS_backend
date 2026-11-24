from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from app.services.companion_caregiver_context import build_companion_caregiver_context


def _caregiver(**overrides):
    values = {
        "id": "caregiver-mary",
        "person_id": "person-mum",
        "name": "Mary Jane Williams",
        "preferred_name": "Mary",
        "relationship": "Daughter",
        "timezone": "America/Los_Angeles",
        "timezone_configured": True,
        "locale": "en-US",
        "date_format": "MM/DD/YYYY",
        "measurement_system": "us_customary",
        "communication_style": "concise",
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _grant(**overrides):
    values = {
        "caregiver_id": "caregiver-mary",
        "person_id": "person-mum",
        "status": "active",
        "revoked_at": None,
        "role": "primary",
        "access_scope": "full_record",
        "permissions": ["view_lcr", "add_evidence"],
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_context_uses_authenticated_preferred_name_and_caregiver_local_time():
    # 2026-11-24 14:00 UTC is 06:00 in Los Angeles (PST).
    context = build_companion_caregiver_context(
        _caregiver(),
        _grant(),
        now=datetime(2026, 11, 24, 14, 0, tzinfo=timezone.utc),
    )
    assert context["display_name"] == "Mary"
    assert context["full_name"] == "Mary Jane Williams"
    assert context["relationship"] == "Daughter"
    assert context["caregiver_role"] == "primary"
    assert context["timezone_name"] == "America/Los_Angeles"
    assert context["timezone_status"] == "configured"
    assert context["local_time"] == "6:00 AM"
    assert context["greeting_period"] == "morning"
    assert context["locale"] == "en-US"
    assert context["date_format"] == "MM/DD/YYYY"
    assert context["measurement_system"] == "us_customary"
    assert context["communication_style"] == "concise"


def test_default_utc_is_not_mistaken_for_a_confirmed_local_timezone():
    context = build_companion_caregiver_context(
        _caregiver(timezone="UTC", timezone_configured=False),
        _grant(),
        now=datetime(2026, 11, 24, 9, 0, tzinfo=timezone.utc),
    )
    assert context["timezone_status"] == "not_configured"
    assert context["timezone_name"] is None
    assert context["local_datetime"] is None
    assert context["greeting_period"] == "neutral"


def test_explicit_utc_can_be_used_when_the_caregiver_configures_it():
    context = build_companion_caregiver_context(
        _caregiver(timezone="UTC", timezone_configured=True),
        _grant(),
        now=datetime(2026, 11, 24, 9, 0, tzinfo=timezone.utc),
    )
    assert context["timezone_status"] == "configured"
    assert context["greeting_period"] == "morning"
    assert context["local_time"] == "9:00 AM"


def test_invalid_timezone_fails_neutral_without_using_server_timezone():
    context = build_companion_caregiver_context(
        _caregiver(timezone="Mars/Olympus_Mons", timezone_configured=True),
        _grant(),
        now=datetime(2026, 11, 24, 9, 0, tzinfo=timezone.utc),
    )
    assert context["timezone_status"] == "invalid"
    assert context["local_datetime"] is None
    assert context["greeting_period"] == "neutral"


def test_conflicting_notification_timezone_suppresses_local_time():
    context = build_companion_caregiver_context(
        _caregiver(),
        _grant(),
        now=datetime(2026, 11, 24, 14, 0, tzinfo=timezone.utc),
        timezone_conflict=True,
    )
    assert context["timezone_status"] == "conflict"
    assert context["local_datetime"] is None
    assert context["greeting_period"] == "neutral"


@pytest.mark.parametrize(
    "grant",
    [
        _grant(status="revoked"),
        _grant(revoked_at=datetime(2026, 11, 24, tzinfo=timezone.utc)),
        _grant(person_id="another-person"),
        _grant(caregiver_id="another-caregiver"),
        _grant(permissions=[]),
    ],
)
def test_context_rejects_missing_revoked_cross_record_or_unpermitted_grants(grant):
    with pytest.raises(ValueError):
        build_companion_caregiver_context(_caregiver(), grant)


def test_missing_caregiver_name_does_not_invent_a_display_name():
    context = build_companion_caregiver_context(
        _caregiver(name=" ", preferred_name=None),
        _grant(),
    )
    assert context["display_name"] is None
    assert context["full_name"] is None
