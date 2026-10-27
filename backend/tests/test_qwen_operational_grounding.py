import pytest

from app.api.collaboration import _contains_unsupported_operational_claim


@pytest.mark.parametrize(
    "reply",
    [
        "Multi-factor authentication is not enabled.",
        "No professional access.",
        "The record is synced.",
        "The reminder was delivered.",
        "The account has been verified.",
    ],
)
def test_rejects_unverified_operational_status_claims(reply):
    assert _contains_unsupported_operational_claim(reply, "Please check the current status.", []) is True


def test_allows_explicit_uncertainty_about_unverified_sync_state():
    reply = "I cannot verify whether the record is synced because no server receipt was supplied."
    assert _contains_unsupported_operational_claim(reply, "Check sync status.", []) is False


def test_allows_status_when_authoritative_context_explicitly_confirms_it():
    assert _contains_unsupported_operational_claim(
        "Multi-factor authentication is enabled.",
        "What is the account security status?",
        ["Authoritative account setting: Multi-factor authentication is enabled."],
    ) is False


def test_rejects_reversed_weekday_correction():
    source = "The weekday was recorded as Monday, but I now know it was Tuesday."
    reply = "The weekday was recorded as Tuesday, but I now know it was Monday."
    assert _contains_unsupported_operational_claim(reply, source, []) is True


def test_accepts_preserved_correction_without_claiming_a_write():
    source = "The weekday was recorded as Monday, but I now know it was Tuesday."
    reply = "The supplied correction says Monday was recorded and Tuesday is the corrected weekday."
    assert _contains_unsupported_operational_claim(reply, source, []) is False


def test_rejects_invented_future_uncertainty_from_past_observation():
    source = "She slept poorly last night."
    reply = "She slept poorly last night. She is unsure if she will be able to sleep tonight."
    assert _contains_unsupported_operational_claim(reply, source, []) is True


def test_rejects_changed_source_attribution():
    source = "Caregiver observed she seemed tired this morning."
    reply = "She reported feeling tired this morning."
    assert _contains_unsupported_operational_claim(reply, source, []) is True


def test_rejects_claim_that_assistant_will_change_the_record():
    assert _contains_unsupported_operational_claim(
        "I can update the record accordingly.",
        "The earlier entry has the wrong date.",
        [],
    ) is True
