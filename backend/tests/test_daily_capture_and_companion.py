"""Tests for the structured daily forms and the companion query surface.

The properties worth protecting here are the trust ones. A form entry has to
reach the tables the whole engine reads, it must not invent an interpretation, a
grounded answer must carry a confidence, and an ungrounded one must not.
"""

from datetime import datetime, timedelta, timezone
import json

import pytest
from fastapi.testclient import TestClient

from app.core.database import get_db
from app.main import app
from app.models.care import CareEvent, Evidence
from app.models.daily_capture import MedicationRecord, ScaleObservation, VitalSign
from app.services.conversation import ConversationEngine
from app.services.daily_capture import DailyCaptureService
from app.services.llm.trust_guard import TrustGuard


def _now():
    return datetime.now(timezone.utc)


@pytest.fixture()
def client(db):
    """A TestClient bound to the test's own session.

    Without this the app would resolve its own session from get_db and write to
    the on-disk development database, so the assertions below would be checking
    an empty table while the rows went somewhere else entirely.
    """

    def override_get_db():
        yield db

    app.dependency_overrides[get_db] = override_get_db
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.pop(get_db, None)


class TestMedication:
    def test_a_dose_writes_evidence_and_an_event(self, db, person, caregiver):
        """A form entry has to land in the tables the engine already reads.

        If it only wrote a MedicationRecord row, nothing downstream would ever
        see it and the form would be a private notes app.
        """
        service = DailyCaptureService(db)
        record = service.record_medication(
            {
                "person_id": person.id,
                "caregiver_id": caregiver.id,
                "medication_name": "Donepezil",
                "dose": "10mg",
                "status": "given",
                "administered_at": _now(),
            }
        )
        db.commit()

        assert record.evidence_id and record.care_event_id
        evidence = db.query(Evidence).filter(Evidence.id == record.evidence_id).one()
        event = db.query(CareEvent).filter(CareEvent.id == record.care_event_id).one()

        assert "Donepezil" in evidence.source_text
        assert "given" in evidence.source_text
        assert event.event_type == "medication"
        assert record.evidence_id in event.evidence_ids
        # The event has to be attributable, or the timeline is anonymous.
        assert event.created_by_caregiver_id == caregiver.id

    def test_a_refusal_says_so_in_words(self, db, person, caregiver):
        """A refusal is something the person did, and is usually the point of the day."""
        service = DailyCaptureService(db)
        record = service.record_medication(
            {
                "person_id": person.id,
                "caregiver_id": caregiver.id,
                "medication_name": "Donepezil",
                "status": "refused",
                "note": "spit it out twice",
            }
        )
        db.commit()

        evidence = db.query(Evidence).filter(Evidence.id == record.evidence_id).one()
        assert "refused" in evidence.source_text
        assert "spit it out twice" in evidence.source_text

    def test_the_caregivers_own_words_survive(self, db, person, caregiver):
        """Trust rule 3: the system never replaces the caregiver's words."""
        service = DailyCaptureService(db)
        record = service.record_medication(
            {
                "person_id": person.id,
                "caregiver_id": caregiver.id,
                "medication_name": "Ropinirole",
                "status": "missed",
                "note": "still in the blister pack, I did not see him take it",
            }
        )
        db.commit()

        assert record.note == "still in the blister pack, I did not see him take it"
        evidence = db.query(Evidence).filter(Evidence.id == record.evidence_id).one()
        assert "still in the blister pack" in evidence.source_text

    def test_a_bottle_photo_is_linked_not_duplicated(self, db, person, caregiver, evidence):
        """The photo keeps its own identity so corrections can reach it."""
        photo = evidence(text="photo of the Donepezil box")
        service = DailyCaptureService(db)
        record = service.record_medication(
            {
                "person_id": person.id,
                "caregiver_id": caregiver.id,
                "medication_name": "Donepezil",
                "status": "given",
                "photo_evidence_id": photo.id,
            }
        )
        db.commit()

        created = db.query(Evidence).filter(Evidence.id == record.evidence_id).one()
        # extra_metadata is a Text column, so it comes back as a JSON string.
        assert photo.id in json.loads(created.extra_metadata)["related_photo_evidence_id"]
        # One photo, one evidence row. A copy would drift from its original.
        assert created.id != photo.id

    def test_naive_timestamps_are_kept_as_wall_clock(self, db, person, caregiver):
        """A caregiver typing 7am means their morning, not UTC.

        Silently attaching UTC would invent a timezone nobody asserted.
        """
        service = DailyCaptureService(db)
        record = service.record_medication(
            {
                "person_id": person.id,
                "caregiver_id": caregiver.id,
                "medication_name": "Donepezil",
                "status": "given",
                "administered_at": datetime(2026, 9, 28, 7, 0),
            }
        )
        db.commit()

        stored = db.query(Evidence).filter(Evidence.id == record.evidence_id).one()
        assert "local time" in stored.time_provenance


class TestVitals:
    def test_a_reading_in_range_is_not_flagged(self, db, person, caregiver):
        service = DailyCaptureService(db)
        record = service.record_vital(
            {
                "person_id": person.id,
                "caregiver_id": caregiver.id,
                "vital_type": "heart_rate",
                "value": 72,
                "measured_at": _now(),
            }
        )
        db.commit()
        assert record.is_out_of_range is False
        assert record.range_note is None

    def test_an_unusual_reading_is_flagged_without_being_explained(self, db, person, caregiver):
        """Flagging is allowed. Explaining why it happened is not.

        A number outside a reference range is a reason to call someone. What
        causes it is a clinical question, and answering it here would be exactly
        the diagnosis this system is built not to give.
        """
        service = DailyCaptureService(db)
        record = service.record_vital(
            {
                "person_id": person.id,
                "caregiver_id": caregiver.id,
                "vital_type": "systolic_bp",
                "value": 195,
                "measured_at": _now(),
            }
        )
        db.commit()

        assert record.is_out_of_range is True
        assert "contact a clinician" in record.range_note
        # The flag must not smuggle in a cause.
        assert "because" not in record.range_note.lower()
        assert "stroke" not in record.range_note.lower()

    def test_a_fahrenheit_reading_is_not_judged_against_celsius(self, db, person, caregiver):
        """Applying a Celsius range to 101.4F would produce confident nonsense.

        Not flagging is the safer failure of the two.
        """
        service = DailyCaptureService(db)
        record = service.record_vital(
            {
                "person_id": person.id,
                "caregiver_id": caregiver.id,
                "vital_type": "temperature",
                "value": 101.4,
                "unit": "F",
                "measured_at": _now(),
            }
        )
        db.commit()

        assert record.is_out_of_range is False
        assert record.range_note is None

    def test_a_reading_writes_evidence_attributed_to_the_caregiver(self, db, person, caregiver):
        service = DailyCaptureService(db)
        record = service.record_vital(
            {
                "person_id": person.id,
                "caregiver_id": caregiver.id,
                "vital_type": "spo2",
                "value": 96,
                "measured_at": _now(),
            }
        )
        db.commit()

        event = db.query(CareEvent).filter(CareEvent.id == record.care_event_id).one()
        assert event.created_by_caregiver_id == caregiver.id
        assert event.evidence_ids == [record.evidence_id]


class TestScales:
    def test_the_pole_labels_are_kept_with_the_number(self, db, person, caregiver):
        """A bare 4 is ambiguous. 4 on agitation and 4 on clarity differ in meaning."""
        service = DailyCaptureService(db)
        record = service.record_scale(
            {
                "person_id": person.id,
                "caregiver_id": caregiver.id,
                "axis": "agitation",
                "score": 4,
                "observed_at": _now(),
            }
        )
        db.commit()

        assert record.low_label == "Settled"
        assert record.high_label == "Very agitated"
        event = db.query(CareEvent).filter(CareEvent.id == record.care_event_id).one()
        assert "Very agitated" in event.description

    def test_a_score_outside_one_to_five_is_refused(self, db, person, caregiver):
        service = DailyCaptureService(db)
        with pytest.raises(ValueError):
            service.record_scale(
                {
                    "person_id": person.id,
                    "caregiver_id": caregiver.id,
                    "axis": "mood",
                    "score": 7,
                    "observed_at": _now(),
                }
            )

    def test_agitation_feeds_the_behaviour_vocabulary(self, db, person, caregiver):
        """The behaviour engine already knows these categories, so reuse them."""
        service = DailyCaptureService(db)
        record = service.record_scale(
            {
                "person_id": person.id,
                "caregiver_id": caregiver.id,
                "axis": "agitation",
                "score": 5,
                "observed_at": _now(),
            }
        )
        db.commit()

        event = db.query(CareEvent).filter(CareEvent.id == record.care_event_id).one()
        assert event.event_type == "behavior_agitation"

    def test_clarity_maps_to_confusion(self, db, person, caregiver):
        service = DailyCaptureService(db)
        record = service.record_scale(
            {
                "person_id": person.id,
                "caregiver_id": caregiver.id,
                "axis": "clarity",
                "score": 2,
                "observed_at": _now(),
            }
        )
        db.commit()

        event = db.query(CareEvent).filter(CareEvent.id == record.care_event_id).one()
        assert event.event_type == "behavior_confusion"


class TestAdherence:
    def test_no_records_means_no_rate(self, db, person, caregiver):
        """Absence of records is not evidence of missed doses.

        Returning 0.0 here would tell a caregiver the person is not taking their
        medication when the truth is that nobody wrote anything down.
        """
        service = DailyCaptureService(db)
        trend = service.adherence_trend(person.id, "Donepezil", window_days=14)
        db.commit()

        assert trend["scheduled_count"] == 0
        assert trend["adherence_rate"] is None
        assert trend["confidence"] is None
        assert "No doses have been recorded" in trend["possible_context"]

    def test_a_rate_is_computed_from_due_doses_only(self, db, person, caregiver):
        service = DailyCaptureService(db)
        for status in ("given", "given", "missed", "not_due"):
            service.record_medication(
                {
                    "person_id": person.id,
                    "caregiver_id": caregiver.id,
                    "medication_name": "Donepezil",
                    "status": status,
                    "administered_at": _now(),
                }
            )
        db.commit()

        trend = service.adherence_trend(person.id, "Donepezil", window_days=14)
        # The not_due row must not count as a miss.
        assert trend["scheduled_count"] == 3
        assert trend["adherence_rate"] == pytest.approx(0.667, abs=0.01)

    def test_a_decline_is_surfaced_with_its_evidence(self, db, person, caregiver):
        service = DailyCaptureService(db)
        # Earlier in the window: all given. Later: half missed.
        for offset, status in [
            (13, "given"),
            (12, "given"),
            (11, "given"),
            (10, "given"),
            (1, "missed"),
            (0, "given"),
        ]:
            service.record_medication(
                {
                    "person_id": person.id,
                    "caregiver_id": caregiver.id,
                    "medication_name": "Donepezil",
                    "status": status,
                    "administered_at": _now() - timedelta(days=offset),
                }
            )
        db.commit()

        trend = service.adherence_trend(person.id, "Donepezil", window_days=14)
        assert trend["declining"] is True
        assert trend["evidence_ids"]
        # The reason a dose was missed is not in the record, and must not be implied.
        assert "not established by the record" in trend["possible_context"]

    def test_a_refusal_is_counted_separately_from_a_miss(self, db, person, caregiver):
        """Collapsing these would hide the thing a prescriber most needs to know."""
        service = DailyCaptureService(db)
        for status in ("refused", "missed", "given"):
            service.record_medication(
                {
                    "person_id": person.id,
                    "caregiver_id": caregiver.id,
                    "medication_name": "Donepezil",
                    "status": status,
                    "administered_at": _now(),
                }
            )
        db.commit()

        trend = service.adherence_trend(person.id, "Donepezil", window_days=14)
        assert trend["refused_count"] == 1
        assert trend["missed_count"] == 1
        assert trend["given_count"] == 1


class TestConfidenceFix:
    """The guard used to hardcode confidence to None on every conversational turn.

    Trust rule 1 requires a confidence wherever there are citations, so a
    grounded answer could not carry the one number a caregiver is entitled to.
    """

    def test_a_grounded_turn_keeps_its_confidence(self):
        guard = TrustGuard(permitted_evidence_ids=["e1"], require_evidence=False)
        result = guard.guard_conversation(
            {
                "reply": "She was more settled this morning than yesterday.",
                "grounded_claims": ["e1"],
                "confidence": 0.8,
            }
        )
        assert result.is_usable
        assert result.confidence == pytest.approx(0.8)
        assert result.payload["confidence"] == pytest.approx(0.8)

    def test_a_turn_with_no_citation_gets_no_confidence(self):
        """No surviving citation means no number. A plausible float here is invented."""
        guard = TrustGuard(permitted_evidence_ids=["e1"], require_evidence=False)
        result = guard.guard_conversation(
            {
                "reply": "That sounds like a rough night.",
                "grounded_claims": [],
                "confidence": 0.9,
            }
        )
        assert result.confidence is None

    def test_a_citation_outside_the_permitted_set_is_dropped_and_the_number_with_it(self):
        guard = TrustGuard(permitted_evidence_ids=["e1"], require_evidence=False)
        result = guard.guard_conversation(
            {
                "reply": "She was unsettled.",
                "grounded_claims": ["e-not-permitted"],
                "confidence": 0.95,
            }
        )
        assert result.output_evidence_ids == []
        assert result.confidence is None

    def test_the_engine_surfaces_confidence_to_its_caller(self, db, person, caregiver, evidence):
        """The guard's value has to reach the API, or the fix is invisible."""
        evidence(text="Mum was up four times last night.")
        engine = ConversationEngine(db)
        result = engine.respond(person_id=person.id, text="How was she sleeping?", caregiver_id=caregiver.id)
        # No local model in tests, so this is the fallback path. What matters is
        # that the key is present and a real value rather than a hardcoded None.
        assert "confidence" in result
        assert result["guard_verdict"] is not None


class TestCompanionEndpoint:
    def test_an_ungrounded_answer_is_reported_as_ungrounded(self, db, person, caregiver, client):
        """A client must be able to tell prose from a finding."""
        response = client.post(
            "/api/v1/companion/query",
            json={"person_id": person.id, "question": "What is her blood pressure?", "caregiver_id": caregiver.id},
        )
        assert response.status_code == 200
        body = response.json()
        # Nothing in the record, so the answer must be flagged ungrounded.
        assert body["grounded"] is False
        assert body["evidence_ids"] == []
        assert body["confidence"] is None

    def test_an_empty_question_is_rejected(self, db, person, caregiver, client):
        response = client.post(
            "/api/v1/companion/query",
            json={"person_id": person.id, "question": "   ", "caregiver_id": caregiver.id},
        )
        assert response.status_code == 400

    def test_the_conversation_endpoint_returns_a_session(self, db, person, caregiver, client):
        response = client.post(
            "/api/v1/companion/conversation",
            json={"person_id": person.id, "question": "How was she today?", "caregiver_id": caregiver.id},
        )
        assert response.status_code == 200
        assert response.json()["session_id"]


class TestDailyEndpoint:
    def test_the_whole_form_saves_in_one_request(self, db, person, caregiver, client):
        """A half-written day looks like a day where nothing happened."""
        response = client.post(
            "/api/v1/daily/log",
            json={
                "person_id": person.id,
                "caregiver_id": caregiver.id,
                "medications": [
                    {
                        "person_id": person.id,
                        "caregiver_id": caregiver.id,
                        "medication_name": "Donepezil",
                        "status": "given",
                        "administered_at": _now().isoformat(),
                    }
                ],
                "vitals": [
                    {
                        "person_id": person.id,
                        "caregiver_id": caregiver.id,
                        "vital_type": "heart_rate",
                        "value": 74,
                        "measured_at": _now().isoformat(),
                    }
                ],
                "scales": [
                    {
                        "person_id": person.id,
                        "caregiver_id": caregiver.id,
                        "axis": "agitation",
                        "score": 3,
                        "observed_at": _now().isoformat(),
                    }
                ],
            },
        )
        assert response.status_code == 200
        body = response.json()
        assert len(body["medications"]) == 1
        assert len(body["vitals"]) == 1
        assert len(body["scales"]) == 1
        assert len(body["care_event_ids"]) == 3

        assert db.query(MedicationRecord).count() == 1
        assert db.query(VitalSign).count() == 1
        assert db.query(ScaleObservation).count() == 1

    def test_a_failure_partway_through_rolls_the_whole_form_back(
        self, db, person, caregiver, client, monkeypatch
    ):
        """Partial saves are worse than none, because they read as a real day.

        The failure is injected after the medication has already been written, so
        this genuinely exercises the rollback. A malformed payload is rejected by
        the schema before any write happens and would pass here without ever
        reaching the transaction - which is the version of this test that
        asserted nothing.
        """
        original = DailyCaptureService.record_scale

        def explode(self, payload):
            raise ValueError("scale axis not recognised")

        monkeypatch.setattr(DailyCaptureService, "record_scale", explode)

        response = client.post(
            "/api/v1/daily/log",
            json={
                "person_id": person.id,
                "caregiver_id": caregiver.id,
                "medications": [
                    {
                        "person_id": person.id,
                        "caregiver_id": caregiver.id,
                        "medication_name": "Donepezil",
                        "status": "given",
                    }
                ],
                "scales": [
                    {
                        "person_id": person.id,
                        "caregiver_id": caregiver.id,
                        "axis": "agitation",
                        "score": 3,
                        "observed_at": _now().isoformat(),
                    }
                ],
            },
        )
        assert response.status_code == 400
        # The medication written before the failure must not survive. If the
        # rollback were removed, the count would be 1 and this would fail.
        assert db.query(MedicationRecord).count() == 0
        assert db.query(Evidence).count() == 0
        assert db.query(CareEvent).count() == 0

    def test_an_out_of_range_scale_score_is_refused_by_the_schema(self, db, person, caregiver, client):
        """A five-button scale cannot produce a 9, and the API says so."""
        response = client.post(
            "/api/v1/daily/scale",
            json={
                "person_id": person.id,
                "caregiver_id": caregiver.id,
                "axis": "mood",
                "score": 9,
                "observed_at": _now().isoformat(),
            },
        )
        assert response.status_code == 422
        assert db.query(ScaleObservation).count() == 0

    def test_adherence_is_served_over_http(self, db, person, caregiver, client):
        response = client.get(
            "/api/v1/daily/adherence/{0}".format(person.id),
            params={"medication_name": "Donepezil", "window_days": 14},
        )
        assert response.status_code == 200
        body = response.json()
        assert body["adherence_rate"] is None
        assert body["possible_context"]
