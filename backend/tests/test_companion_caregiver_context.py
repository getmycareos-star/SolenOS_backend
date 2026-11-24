from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from app.services.companion_caregiver_context import (
    build_companion_caregiver_context,
    build_companion_prompt_identity_context,
)


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
    assert context["local_date"] == "2026-11-24"
    assert context["local_day_of_week"] == "Tuesday"
    assert context["local_utc_offset"] == "-0800"
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


def test_companion_context_uses_caregiver_timezone_without_notification_preference_input():
    context = build_companion_caregiver_context(
        _caregiver(timezone="America/Los_Angeles", timezone_configured=True),
        _grant(),
        now=datetime(2026, 11, 24, 14, 0, tzinfo=timezone.utc),
    )
    assert context["timezone_status"] == "configured"
    assert context["timezone_name"] == "America/Los_Angeles"
    assert context["local_time"] == "6:00 AM"


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


def test_context_uses_canonical_member_manager_permission_semantics():
    context = build_companion_caregiver_context(
        _caregiver(),
        _grant(permissions=[], can_manage_members=True),
    )
    assert context["caregiver_id"] == "caregiver-mary"
    assert context["access_scope"] == "full_record"
    assert "view_lcr" in context["permissions"]
    assert "correct_records" in context["permissions"]


def test_documents_only_grant_cannot_build_companion_care_context():
    with pytest.raises(ValueError, match="not permitted to view this care record"):
        build_companion_caregiver_context(
            _caregiver(),
            _grant(access_scope="documents_only", permissions=["view_documents"]),
        )


def test_missing_caregiver_name_does_not_invent_a_display_name():
    context = build_companion_caregiver_context(
        _caregiver(name=" ", preferred_name=None),
        _grant(),
    )
    assert context["display_name"] is None
    assert context["full_name"] is None


@pytest.mark.parametrize(
    "utc_hour,expected_period",
    [
        (4, "neutral"), (5, "morning"), (11, "morning"),
        (12, "afternoon"), (16, "afternoon"), (17, "evening"),
        (20, "evening"), (21, "neutral"), (23, "neutral"),
    ],
)
def test_greeting_period_boundaries_are_calculated_in_caregiver_timezone(utc_hour, expected_period):
    context = build_companion_caregiver_context(
        _caregiver(timezone="UTC", timezone_configured=True),
        _grant(),
        now=datetime(2026, 11, 24, utc_hour, 0, tzinfo=timezone.utc),
    )
    assert context["greeting_period"] == expected_period


def test_daylight_saving_transition_uses_iana_timezone_rules():
    before = build_companion_caregiver_context(
        _caregiver(timezone="America/Los_Angeles", timezone_configured=True),
        _grant(),
        now=datetime(2026, 3, 8, 9, 30, tzinfo=timezone.utc),
    )
    after = build_companion_caregiver_context(
        _caregiver(timezone="America/Los_Angeles", timezone_configured=True),
        _grant(),
        now=datetime(2026, 3, 8, 10, 30, tzinfo=timezone.utc),
    )
    assert before["local_time"] == "1:30 AM"
    assert after["local_time"] == "3:30 AM"
    assert before["timezone_name"] == after["timezone_name"] == "America/Los_Angeles"


def test_two_caregivers_sharing_one_record_receive_only_their_own_identity():
    first = build_companion_caregiver_context(
        _caregiver(id="caregiver-a", person_id="shared-person", name="Mary Johnson", preferred_name="Mary"),
        _grant(caregiver_id="caregiver-a", person_id="shared-person"),
    )
    second = build_companion_caregiver_context(
        _caregiver(id="caregiver-b", person_id="shared-person", name="John Smith", preferred_name="John"),
        _grant(caregiver_id="caregiver-b", person_id="shared-person"),
    )
    assert first["display_name"] == "Mary"
    assert second["display_name"] == "John"
    assert first["caregiver_id"] != second["caregiver_id"]


def test_context_serializes_only_supplied_caregiver_operational_records_without_upgrading_status():
    profile = SimpleNamespace(
        participation_category=SimpleNamespace(value="family"),
        involvement_status=SimpleNamespace(value="active"),
        availability_status=SimpleNamespace(value="available"),
        is_primary_designated=True,
    )
    responsibility = SimpleNamespace(
        responsibility_type="medication coordination",
        scope="Review the medication list before appointments",
        status=SimpleNamespace(value="ambiguous"),
        acceptance_status=SimpleNamespace(value="unconfirmed"),
        effective_start=datetime(2026, 1, 1, tzinfo=timezone.utc),
        effective_end=None,
        assignment_source="reported",
        is_joint=False,
        schedule_pattern=None,
        provenance="Caregiver reported this responsibility",
    )
    role = SimpleNamespace(
        role_type=SimpleNamespace(value="primary"),
        custom_role_name=None,
        scope="Coordinate family care",
        effective_start=datetime(2026, 1, 1, tzinfo=timezone.utc),
        effective_end=None,
        assignment_source="caregiver_confirmed",
        is_current=True,
    )
    loop = SimpleNamespace(
        id="loop-1",
        loop_type="follow_up",
        title="Confirm pharmacy refill",
        description="Check whether the refill is ready",
        status="waiting",
        outcome_status="outcome_unknown",
        owner_role="caregiver",
        owner_label="Mary",
        uncertainty="unknown",
        missing_information=["Pharmacy response"],
        request_text="Please call the pharmacy",
        recipient_label="Care recipient",
        deadline_at=None,
        deadline_timezone=None,
        deadline_source=None,
        priority="normal",
        priority_reasons=[],
        deferred_reason=None,
        review_at=None,
        consequence_of_delay="moderate",
        verification_rule="Record the pharmacy response",
    )
    context = build_companion_caregiver_context(
        _caregiver(),
        _grant(),
        caregiver_profile=profile,
        active_roles=[role],
        active_responsibilities=[responsibility],
        assigned_open_loops=[loop],
        responsibilities_truncated=True,
        open_loops_truncated=False,
    )
    assert context["caregiver_profile"]["is_primary_designated"] is True
    assert context["active_roles"][0]["role_type"] == "primary"
    assert context["active_responsibilities"][0]["status"] == "ambiguous"
    assert context["active_responsibilities"][0]["acceptance_status"] == "unconfirmed"
    assert context["active_responsibilities_status"] == "available"
    assert context["active_responsibilities_truncated"] is True
    assert context["assigned_open_loops"][0]["status"] == "waiting"
    assert context["assigned_open_loops_status"] == "available"


def test_companion_prompt_identity_binds_authenticated_caregiver_to_named_recipient_and_grant():
    caregiver = build_companion_caregiver_context(
        _caregiver(),
        _grant(),
        now=datetime(2026, 11, 24, 14, 0, tzinfo=timezone.utc),
    )
    identity = build_companion_prompt_identity_context(caregiver, "Margaret Williams")
    assert identity["identity_context_status"] == "authenticated"
    assert identity["caregiver_display_name"] == "Mary"
    assert identity["care_recipient_name"] == "Margaret Williams"
    assert identity["caregiver_relationship"] == "Daughter"
    assert identity["caregiver_role"] == "primary"
    assert identity["access_scope"] == "full_record"
    assert identity["permitted_capabilities"] == ["view_lcr", "add_evidence"]
    assert identity["timezone_name"] == "America/Los_Angeles"
    assert identity["local_datetime"] == "2026-11-24T06:00:00-08:00"
    assert identity["local_date"] == "2026-11-24"
    assert identity["local_day_of_week"] == "Tuesday"
    assert identity["local_utc_offset"] == "-0800"
    assert identity["timezone_status"] == "configured"


def test_companion_greeting_identity_never_discloses_care_recipient_or_care_permissions():
    caregiver = build_companion_caregiver_context(_caregiver(), _grant())
    identity = build_companion_prompt_identity_context(caregiver, "Margaret Williams", greeting_only=True)
    assert identity["identity_context_status"] == "authenticated_caregiver_only"
    assert identity["caregiver_display_name"] == "Mary"
    assert identity["care_recipient_name"] is None
    assert identity["caregiver_relationship"] is None
    assert identity["caregiver_role"] is None
    assert identity["access_scope"] is None
    assert identity["permitted_capabilities"] == []
    assert "Margaret Williams" not in str(identity)


def test_product_identity_question_receives_no_personal_identity_context():
    caregiver = build_companion_caregiver_context(_caregiver(), _grant())
    identity = build_companion_prompt_identity_context(caregiver, "Margaret Williams", identity_question=True)
    assert identity == {"identity_context_status": "omitted_for_product_identity_question"}


def test_unconfigured_timezone_is_explicitly_unknown_in_companion_identity_envelope():
    caregiver = build_companion_caregiver_context(
        _caregiver(timezone="UTC", timezone_configured=False),
        _grant(),
    )
    identity = build_companion_prompt_identity_context(caregiver, "Margaret Williams")
    assert identity["timezone_status"] == "not_configured"
    assert identity["timezone_name"] is None
    assert identity["local_date"] is None
    assert identity["local_time"] is None


def test_authenticated_account_identity_is_retained_in_backend_context_not_model_envelope():
    context = build_companion_caregiver_context(
        _caregiver(),
        _grant(),
        authenticated_account_id="account-123",
    )
    assert context["authenticated_account_id"] == "account-123"

    identity = build_companion_prompt_identity_context(context, "Margaret Williams")
    assert identity["caregiver_display_name"] == "Mary"
    assert "authenticated_account_id" not in identity
    assert "account-123" not in str(identity)


def test_notification_preferences_are_delivery_only_and_excluded_from_greetings():
    preferences = SimpleNamespace(
        quiet_hours_enabled=True,
        quiet_hours_start="21:00",
        quiet_hours_end="08:00",
        timezone_name="America/Chicago",
        reminders_enabled=False,
    )
    context = build_companion_caregiver_context(
        _caregiver(timezone="America/New_York", timezone_configured=True),
        _grant(),
        notification_preferences=preferences,
        now=datetime(2026, 11, 24, 14, 0, tzinfo=timezone.utc),
    )
    assert context["timezone_name"] == "America/New_York"
    assert context["notification_preferences"] == {
        "quiet_hours_enabled": True,
        "quiet_hours_start": "21:00",
        "quiet_hours_end": "08:00",
        "delivery_timezone_name": "America/Chicago",
        "reminders_enabled": False,
        "configured": True,
    }

    care_identity = build_companion_prompt_identity_context(context, "Margaret Williams")
    assert care_identity["notification_preferences"]["delivery_timezone_name"] == "America/Chicago"

    greeting_identity = build_companion_prompt_identity_context(
        context, "Margaret Williams", greeting_only=True,
    )
    assert "notification_preferences" not in greeting_identity


def test_missing_notification_preferences_are_explicitly_unconfigured():
    context = build_companion_caregiver_context(_caregiver(), _grant())
    assert context["notification_preferences"]["configured"] is False
    assert context["notification_preferences"]["delivery_timezone_name"] is None



def test_companion_context_marks_first_time_stage_from_server_supplied_history_state():
    context = build_companion_caregiver_context(
        _caregiver(),
        _grant(),
        has_prior_conversation=False,
    )
    assert context["conversation_stage"] == "first_time"
    identity = build_companion_prompt_identity_context(
        context, "Margaret Williams", greeting_only=True,
    )
    assert identity["conversation_stage"] == "first_time"
    assert identity["identity_context_status"] == "authenticated_caregiver_only"
    assert "Margaret Williams" not in str(identity)


def test_companion_context_marks_returning_stage_without_injecting_conversation_content():
    context = build_companion_caregiver_context(
        _caregiver(),
        _grant(),
        has_prior_conversation=True,
    )
    assert context["conversation_stage"] == "returning"
    identity = build_companion_prompt_identity_context(
        context, "Margaret Williams", greeting_only=True,
    )
    assert identity["conversation_stage"] == "returning"
    assert "prior" not in str(identity).casefold()
    assert "Margaret Williams" not in str(identity)


def test_companion_context_keeps_conversation_stage_unknown_when_history_was_not_checked():
    context = build_companion_caregiver_context(_caregiver(), _grant())
    assert context["conversation_stage"] == "unknown"



def test_generic_family_relationship_default_is_not_misrepresented_as_recorded_relationship():
    caregiver = _caregiver(relationship="Family")
    context = build_companion_caregiver_context(caregiver, _grant())
    assert context["relationship"] is None

    identity = build_companion_prompt_identity_context(context, "Margaret Williams")
    assert identity["caregiver_relationship"] is None


def test_specific_recorded_caregiver_relationship_is_preserved():
    caregiver = _caregiver(relationship="Daughter")
    context = build_companion_caregiver_context(caregiver, _grant())
    assert context["relationship"] == "Daughter"
