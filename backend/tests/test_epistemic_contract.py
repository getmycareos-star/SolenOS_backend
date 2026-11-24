import pytest

from app.services.epistemic_contract import assertion_information_status


@pytest.mark.parametrize(
    ("epistemic_state", "assertion_status", "expected"),
    [
        ("reported", "reported", "caregiver_reported_assertion"),
        ("reported", "accepted", "caregiver_reviewed_report"),
        ("reported", "caregiver_reviewed", "caregiver_reviewed_report"),
        ("observed", "caregiver_reviewed", "source_attributed_observation"),
        ("inferred", "reported", "inferred_interpretation"),
        ("inferred", "accepted", "inferred_interpretation"),
        ("inferred", "caregiver_reviewed", "inferred_interpretation"),
        ("verified", "accepted", "verified"),
        ("uncertain", "accepted", "uncertain"),
        ("conflicting", "accepted", "conflicting_information"),
        ("missing", "accepted", "missing"),
        ("reported", "superseded", "historical_assertion"),
        ("inferred", "withdrawn", "historical_assertion"),
        ("verified", "rejected", "historical_assertion"),
        ("conflicting", "superseded", "historical_assertion"),
        ("verified", "retracted", "historical_assertion"),
    ],
)
def test_epistemic_state_is_independent_of_review_lifecycle(
    epistemic_state: str,
    assertion_status: str,
    expected: str,
):
    information_status, interpretation_limit = assertion_information_status(
        epistemic_state,
        assertion_status,
    )

    assert information_status == expected
    assert interpretation_limit
    if epistemic_state == "inferred":
        assert "not" in interpretation_limit.lower()
        assert "verified" in interpretation_limit.lower()
    if epistemic_state == "reported" and assertion_status in {"accepted", "caregiver_reviewed"}:
        assert "does not establish" in interpretation_limit
