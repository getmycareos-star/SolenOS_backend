"""Integration tests for authenticated reasoning endpoints."""
import pytest

from app.core.identity_enums import AccountType
from app.core.security import AuthenticatedPrincipal, get_current_active_user
from app.main import app
from app.models.identity import Account, Person

PERSON_ID_1 = "11111111-1111-4111-8111-111111111111"
PERSON_ID_2 = "22222222-2222-4222-8222-222222222222"


@pytest.fixture()
def reasoning_client(client):
    def override_current_user():
        account = Account(
            id="test-account",
            is_active=True,
            account_type=AccountType.CAREGIVER,
        )
        return AuthenticatedPrincipal(account, [PERSON_ID_1, PERSON_ID_2])

    app.dependency_overrides[get_current_active_user] = override_current_user
    return client


def test_causal_inference_endpoint(reasoning_client, db):
    db.add(Person(id=PERSON_ID_1, name="Test Person"))
    db.commit()

    response = reasoning_client.post(
        f"/reasoning/graph/{PERSON_ID_1}/counterfactual",
        json={"intervention": {"medication": "drugA"}},
    )
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["person_id"] == PERSON_ID_1
    assert data["intervention"]["medication"] == "drugA"


def test_belief_updater_endpoint(reasoning_client, db):
    db.add(Person(id=PERSON_ID_2, name="Test Person 2"))
    db.commit()

    response = reasoning_client.post(f"/reasoning/belief/{PERSON_ID_2}/update")
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["person_id"] == PERSON_ID_2
    assert "belief_state_id" in data
    assert "state_variables" in data
    assert "updated_at" in data


def test_explainability_does_not_disclose_unknown_decisions(reasoning_client):
    response = reasoning_client.get(
        "/reasoning/explain/33333333-3333-4333-8333-333333333333"
    )
    assert response.status_code == 404, response.text



def test_daily_intelligence_uses_persisted_records_and_open_care_state(reasoning_client, db):
    """The API must expose stored records, not return an empty success fallback."""
    from datetime import datetime, timedelta, timezone
    from app.core.daily_capture_enums import MedicationStatus, VitalType
    from app.models.caregiver import Caregiver
    from app.models.daily_capture import MedicationRecord, VitalSign
    from app.models.follow_up import FollowUp
    from app.models.uncertainty import OpenQuestion, Contradiction
    from app.models.time_location import CareWindow

    now = datetime.now(timezone.utc)
    person_id = PERSON_ID_1
    person = Person(id=person_id, name="Daily intelligence integration person")
    caregiver = Caregiver(
        id="daily-intelligence-caregiver",
        name="Daily Intelligence Caregiver",
        email="daily-intelligence-caregiver@example.com",
        relationship="daughter",
        person_id=person_id,
    )
    db.add_all([person, caregiver])
    db.flush()

    db.add_all([
        MedicationRecord(
            id="daily-intelligence-medication",
            person_id=person_id,
            medication_name="Example prescribed medication",
            status=MedicationStatus.REFUSED,
            administered_at=now - timedelta(minutes=30),
            recorded_by_caregiver_id=caregiver.id,
            note="Person declined; recorded without interpreting the reason.",
        ),
        VitalSign(
            id="daily-intelligence-vital",
            person_id=person_id,
            vital_type=VitalType.SYSTOLIC_BP,
            value=195.0,
            unit="mmHg",
            measured_at=now - timedelta(minutes=20),
            is_out_of_range=True,
            range_note="Outside configured attention range; not a diagnosis.",
        ),
        FollowUp(
            id="daily-intelligence-overdue-followup",
            person_id=person_id,
            what_to_recheck="Confirm whether the clinic returned the call",
            due_at=now - timedelta(hours=2),
            priority_hint="high",
            evidence_ids=["evidence-clinic-call"],
        ),
        OpenQuestion(
            id="daily-intelligence-open-question",
            person_id=person_id,
            question="Was the appointment date confirmed?",
            subject_type="appointment",
            subject_id="appointment-unknown",
            status="open",
        ),
        Contradiction(
            id="daily-intelligence-contradiction",
            person_id=person_id,
            subject_type="medication",
            subject_id="medication-example",
            field="dose",
            description="Two reports give different doses; neither has been selected as authoritative.",
            resolution_status="unresolved",
            evidence_ids=["evidence-dose-a", "evidence-dose-b"],
        ),
        CareWindow(
            id="daily-intelligence-active-window",
            person_id=person_id,
            window_type="follow_up_window",
            started_at=now - timedelta(minutes=10),
            ends_at=now + timedelta(hours=1),
            status="active",
            time_provenance="explicitly_recorded",
        ),
        CareWindow(
            id="daily-intelligence-expired-window",
            person_id=person_id,
            window_type="follow_up_window",
            started_at=now - timedelta(days=2),
            ends_at=now - timedelta(days=1),
            status="active",
            time_provenance="explicitly_recorded",
        ),
    ])
    db.commit()

    response = reasoning_client.get(f"/reasoning/daily-intelligence/{person_id}")
    assert response.status_code == 200, response.text
    payload = response.json()

    assert payload["person_id"] == person_id
    assert payload["overdue_items"][0]["id"] == "daily-intelligence-overdue-followup"
    assert payload["open_questions"][0]["id"] == "daily-intelligence-open-question"
    assert payload["unresolved_contradictions"][0]["id"] == "daily-intelligence-contradiction"
    assert payload["active_windows"][0]["id"] == "daily-intelligence-active-window"
    assert payload["expired_windows"][0]["id"] == "daily-intelligence-expired-window"

    medication = payload["recent_activity"]["medication_records"]
    assert len(medication) == 1
    assert medication[0]["status"] == "refused"
    assert medication[0]["note"].startswith("Person declined")

    vitals = payload["recent_activity"]["vitals"]
    assert len(vitals) == 1
    assert vitals[0]["vital_type"] == "systolic_bp"
    assert vitals[0]["value"] == 195.0
    assert vitals[0]["is_out_of_range"] is True

    assert "1 overdue follow-up(s)" in payload["daily_summary"]
    assert "1 unresolved contradiction(s)" in payload["daily_summary"]
    assert "does not choose a side" in payload["daily_summary"]
    assert "no clinical conclusion was inferred" in payload["daily_summary"]


def test_daily_intelligence_denies_an_unlinked_person(reasoning_client, db):
    other = Person(id="33333333-3333-4333-8333-333333333333", name="Unlinked person")
    db.add(other)
    db.commit()

    response = reasoning_client.get(f"/reasoning/daily-intelligence/{other.id}")
    assert response.status_code == 403
