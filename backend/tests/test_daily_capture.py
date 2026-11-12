import hashlib
import json
from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.database import Base, get_db
from app.main import app
from app.models.anonymous_session import AnonymousSession
from app.models.care import CareEvent, Caregiver, Correction, Evidence, Insight, Person
from app.models.care_continuity import CareRecordRelationship
from app.models.memory import ReasoningMemory
from app.services.learning import LearningEngine

engine = create_engine(
    "sqlite:///:memory:",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def override_get_db():
    db = TestingSessionLocal()
    try:
        yield db
    finally:
        db.close()


def setup_db():
    Base.metadata.create_all(bind=engine)
    app.dependency_overrides[get_db] = override_get_db


def teardown_db():
    app.dependency_overrides.clear()
    Base.metadata.drop_all(bind=engine)


def seed_person_and_caregiver():
    db = TestingSessionLocal()
    person = Person(id="person-daily-test", name="Daily Capture Test Person")
    caregiver = Caregiver(
        id="caregiver-daily-test",
        name="Daily Capture Test Caregiver",
        email="daily-capture-test@example.invalid",
        relationship="Family",
        person_id=person.id,
    )
    db.add_all([person, caregiver])
    db.commit()
    db.close()




def session_headers(person_id: str, caregiver_id: str) -> dict[str, str]:
    token = f"daily-test-token-{person_id}-{caregiver_id}"
    token_hash = hashlib.sha256(token.encode()).hexdigest()
    db = TestingSessionLocal()
    existing = db.query(AnonymousSession).filter_by(token_hash=token_hash).one_or_none()
    if existing is None:
        db.add(AnonymousSession(
            person_id=person_id,
            caregiver_id=caregiver_id,
            token_hash=token_hash,
            expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
        ))
    else:
        existing.expires_at = datetime.now(timezone.utc) + timedelta(hours=1)
    db.commit()
    db.close()
    return {"Authorization": f"Bearer {token}"}


def test_daily_capture_persists_one_authorized_source_across_history_and_lcr():
    setup_db()
    try:
        seed_person_and_caregiver()
        client = TestClient(app)
        headers = session_headers("person-daily-test", "caregiver-daily-test")
        response = client.post(
            "/api/v1/daily-care/entries",
            headers=headers,
            json={
                "person_id": "person-daily-test",
                "caregiver_id": "caregiver-daily-test",
                "mood": "Calm",
                "general_notes": "User-recorded note",
            },
        )
        assert response.status_code == 200, response.text
        receipt = response.json()
        assert receipt["success"] is True
        assert len(receipt["evidence_ids"]) == 1

        event_id = receipt["id"]
        evidence_id = receipt["evidence_ids"][0]
        db = TestingSessionLocal()
        event = db.query(CareEvent).filter_by(id=event_id).one()
        evidence = db.query(Evidence).filter_by(id=evidence_id).one()
        assert event.person_id == evidence.person_id == "person-daily-test"
        assert event.created_by_caregiver_id == evidence.reported_by_caregiver_id == "caregiver-daily-test"
        assert event.evidence_ids == [evidence.id]
        assert evidence.evidence_kind == "caregiver_report"
        assert evidence.epistemic_status == "caregiver_reported"
        assert evidence.reported_by_label == "Caregiver"
        source_payload = json.loads(evidence.source_text)
        assert source_payload["original_capture"]["general_notes"] == "User-recorded note"
        db.close()

        # Recent History and the LCR must expose the same persisted event ID.
        history = client.get("/api/v1/events/person-daily-test", headers=headers)
        assert history.status_code == 200, history.text
        history_matches = [item for item in history.json() if item["id"] == event_id]
        assert len(history_matches) == 1
        assert history_matches[0]["evidence_ids"] == [evidence_id]

        current_care = client.get("/api/v1/care-continuity/current-care", headers=headers)
        assert current_care.status_code == 200, current_care.text
        lcr_matches = [item for item in current_care.json()["items"] if item["id"] == event_id]
        assert len(lcr_matches) == 1
        assert lcr_matches[0]["evidence_ids"] == [evidence_id]
        assert lcr_matches[0]["evidence_link_validation"] == "same_care_record_evidence_reference_verified"
        assert lcr_matches[0]["source_event_type"] == "daily_care_entry"
        assert lcr_matches[0]["source_label"] == "Caregiver"
        assert lcr_matches[0]["source_type"] == "caregiver_report"
        assert lcr_matches[0]["epistemic_status"] == "caregiver_reported"

        # The public evidence endpoint must serialize the caregiver-reported status.
        evidence_response = client.get("/api/v1/evidence/person-daily-test", headers=headers)
        assert evidence_response.status_code == 200, evidence_response.text
        evidence_matches = [item for item in evidence_response.json() if item["id"] == evidence_id]
        assert len(evidence_matches) == 1
        assert evidence_matches[0]["epistemic_status"] == "caregiver_reported"

        # Rebuild and persist situation understanding from the same verified
        # source set; the assessment must retain the original event/evidence IDs.
        reassessed = client.post(
            "/api/v1/care-continuity/situation-understanding/reassess",
            headers=headers,
        )
        assert reassessed.status_code == 200, reassessed.text
        assessment = reassessed.json()["assessment"]
        assessed_records = [
            item for item in assessment["current_care_reality"]["records"]
            if item.get("id") == event_id
        ]
        assert len(assessed_records) == 1
        assert assessed_records[0]["evidence_ids"] == [evidence_id]
        assert assessed_records[0]["evidence_link_validation"] == "same_care_record_evidence_reference_verified"
        assert assessed_records[0]["source_label"] == "Caregiver"
        assert assessed_records[0]["source_type"] == "caregiver_report"
        assert assessed_records[0]["epistemic_status"] == "caregiver_reported"
        assert any(
            item["record_id"] == event_id and item["source"] == "events"
            for item in assessment["situation_trajectory"]["entries"]
        )

        persisted_assessment = client.get(
            "/api/v1/care-continuity/situation-understanding",
            headers=headers,
        )
        assert persisted_assessment.status_code == 200, persisted_assessment.text
        assert persisted_assessment.json()["assessment"]["person_id"] == "person-daily-test"
        persisted_records = [
            item for item in persisted_assessment.json()["assessment"]["current_care_reality"]["records"]
            if item.get("id") == event_id
        ]
        assert len(persisted_records) == 1
        assert persisted_records[0]["evidence_ids"] == [evidence_id]

        seed_second_person_and_caregiver()
        other_headers = session_headers("person-other-test", "caregiver-other-test")
        foreign_history = client.get("/api/v1/events/person-daily-test", headers=other_headers)
        assert foreign_history.status_code == 403
    finally:
        teardown_db()


def test_lcr_groups_observation_extracted_from_same_evidence_under_source_event(monkeypatch):
    from app.api import care_continuity as care_continuity_api

    setup_db()
    db = TestingSessionLocal()
    try:
        seed_person_and_caregiver()
        evidence = Evidence(
            id="grouped-source-evidence",
            person_id="person-daily-test",
            type="note",
            evidence_kind="caregiver_report",
            epistemic_status="caregiver_reported",
            source_text="Caregiver reports that she has been unusually sad.",
            uploaded_by_caregiver_id="caregiver-daily-test",
            reported_by_caregiver_id="caregiver-daily-test",
            reported_by_label="Caregiver",
            source_type="caregiver_report",
        )
        db.add(evidence)
        db.flush()
        event = {
            "id": "grouped-source-event",
            "event_type": "daily_care_entry",
            "status": "recorded",
            "title": "Daily care entry",
            "description": "Caregiver reports that she has been unusually sad.",
            "occurred_at": "2026-11-03T09:00:00+00:00",
            "recorded_at": "2026-11-03T09:01:00+00:00",
            "created_by_caregiver_id": "caregiver-daily-test",
            "evidence_ids": [evidence.id],
        }
        observation = {
            "id": "grouped-source-observation",
            "evidence_id": evidence.id,
            "observation_kind": "caregiver_report",
            "epistemic_status": "uncertain",
            "subject_label": "Care recipient",
            "attribute": "mood",
            "value_text": "Unusually sad",
            "context": "After the usual evening routine",
            "observer_label": "family caregiver",
            "observation_method": "caregiver report",
            "observed_at": "2026-11-03T09:00:00+00:00",
            "recorded_at": "2026-11-03T09:01:00+00:00",
            "source_passage": "unusually sad",
            "source_span_start": 33,
            "source_span_end": 46,
            "extraction_method": "caregiver_reviewed",
        }
        monkeypatch.setattr(
            care_continuity_api,
            "_scoped_identity",
            lambda request, session: ("person-daily-test", "caregiver-daily-test"),
        )
        monkeypatch.setattr(
            care_continuity_api,
            "build_lcr_context",
            lambda session, person_id, limit_per_source: {
                "person_id": person_id,
                "as_of": "2026-11-03T09:01:00+00:00",
                "history_coverage": "bounded_subset",
                "schema_status": "complete",
                "missing_required_sources": [],
                "missing_optional_sources": [],
                "sources": {
                    "events": {"items": [event]},
                    "evidence_observations": {"items": [observation]},
                },
            },
        )

        projection = care_continuity_api.current_care_projection(
            request=None, limit_per_source=10, db=db
        )
        event_items = [item for item in projection["items"] if item["id"] == event["id"]]
        assert len(event_items) == 1
        assert event_items[0]["evidence_ids"] == [evidence.id]
        assert event_items[0]["source_label"] == "Caregiver"
        assert len(event_items[0]["derived_observations"]) == 1
        assert event_items[0]["derived_observations"][0]["id"] == observation["id"]
        assert event_items[0]["derived_observations"][0]["source_passage"] == "unusually sad"
        assert event_items[0]["derived_observations"][0]["context"] == "After the usual evening routine"
        assert event_items[0]["derived_observations"][0]["observer_label"] == "family caregiver"
        assert event_items[0]["derived_observations"][0]["observation_method"] == "caregiver report"
        assert not any(
            item["source_collection"] == "evidence_observations"
            and item["id"] == observation["id"]
            for item in projection["items"]
        )
    finally:
        db.close()
        teardown_db()


def test_structured_daily_capture_retry_is_idempotent_and_changed_payload_conflicts():
    setup_db()
    try:
        seed_person_and_caregiver()
        client = TestClient(app)
        headers = session_headers("person-daily-test", "caregiver-daily-test")
        payload = {
            "person_id": "person-daily-test",
            "caregiver_id": "caregiver-daily-test",
            "medications": [],
            "vitals": [],
            "scales": [],
            "notes": "Caregiver reported a disrupted sleep routine.",
            "capture_idempotency_key": "daily-capture-request-001",
        }
        first = client.post("/api/v1/daily/log", headers=headers, json=payload)
        assert first.status_code == 200, first.text
        first_receipt = first.json()
        assert first_receipt["success"] is True
        assert first_receipt["idempotent_replay"] is False
        assert len(first_receipt["evidence_ids"]) == 1

        replay = client.post("/api/v1/daily/log", headers=headers, json=payload)
        assert replay.status_code == 200, replay.text
        replay_receipt = replay.json()
        assert replay_receipt["id"] == first_receipt["id"]
        assert replay_receipt["evidence_ids"] == first_receipt["evidence_ids"]
        assert replay_receipt["idempotent_replay"] is True
        assert replay_receipt["qwen_analysis_id"] == first_receipt["qwen_analysis_id"]
        assert replay_receipt["qwen_extraction_status"] == first_receipt["qwen_extraction_status"]

        changed = client.post(
            "/api/v1/daily/log",
            headers=headers,
            json={**payload, "notes": "A different report under the same request key."},
        )
        assert changed.status_code == 409, changed.text

        db = TestingSessionLocal()
        try:
            events = db.query(CareEvent).filter(
                CareEvent.person_id == "person-daily-test",
                CareEvent.event_type == "structured_daily_capture",
            ).all()
            evidence = db.query(Evidence).filter(
                Evidence.person_id == "person-daily-test",
                Evidence.ingestion_key == "daily-capture:caregiver-daily-test:daily-capture-request-001",
            ).all()
            assert len(events) == 1
            assert len(evidence) == 1
            assert events[0].evidence_ids == [evidence[0].id]
        finally:
            db.close()
    finally:
        teardown_db()


def test_legacy_daily_capture_evidence_backfill_is_dry_run_by_default_and_idempotent():
    from app.services.daily_capture_evidence_backfill import backfill_daily_capture_evidence

    setup_db()
    db = TestingSessionLocal()
    try:
        seed_person_and_caregiver()
        original_source = '{"notes":"Original caregiver wording","mood":"Calm"}'
        event = CareEvent(
            id="legacy-daily-capture-event",
            person_id="person-daily-test",
            event_type="structured_daily_capture",
            status="recorded",
            occurred_at=datetime(2026, 10, 20, 9, 0, tzinfo=timezone.utc),
            title="Daily care update",
            description=original_source,
            created_by_caregiver_id="caregiver-daily-test",
        )
        empty_event = CareEvent(
            id="legacy-daily-capture-no-source",
            person_id="person-daily-test",
            event_type="daily_care_entry",
            status="recorded",
            occurred_at=datetime(2026, 10, 21, 9, 0, tzinfo=timezone.utc),
            title="Daily care update without source text",
            description=None,
            created_by_caregiver_id="caregiver-daily-test",
        )
        db.add_all([event, empty_event])
        db.commit()

        dry_run = backfill_daily_capture_evidence(db, apply=False)
        assert dry_run["mode"] == "dry_run"
        assert dry_run["would_create"] == 1
        assert dry_run["skipped_missing_source_text"] == 1
        db.refresh(event)
        assert event.evidence_ids in (None, [])
        assert db.query(Evidence).filter_by(person_id="person-daily-test").count() == 0

        applied = backfill_daily_capture_evidence(db, apply=True)
        assert applied["created"] == 1
        assert applied["skipped_missing_source_text"] == 1
        db.refresh(event)
        assert len(event.evidence_ids) == 1
        evidence = db.query(Evidence).filter_by(id=event.evidence_ids[0]).one()
        assert evidence.source_text == original_source
        assert evidence.evidence_kind == "caregiver_report"
        assert evidence.epistemic_status == "caregiver_reported"
        assert evidence.reported_by_caregiver_id == "caregiver-daily-test"
        assert evidence.ingestion_key == "legacy-daily-capture:legacy-daily-capture-event"

        repeated = backfill_daily_capture_evidence(db, apply=True)
        assert repeated["created"] == 0
        assert repeated["already_linked"] == 1
        assert db.query(Evidence).filter_by(person_id="person-daily-test").count() == 1
    finally:
        db.close()
        teardown_db()


def test_quick_daily_checkin_persists_selected_scope_and_caregiver_attribution():
    setup_db()
    try:
        seed_person_and_caregiver()
        client = TestClient(app)
        response = client.post(
            "/api/v1/daily/log",
            headers=session_headers("person-daily-test", "caregiver-daily-test"),
            json={
                "person_id": "person-daily-test",
                "caregiver_id": "caregiver-daily-test",
                "medications": [],
                "vitals": [],
                "scales": [],
                "notes": (
                    "Caregiver reports no significant change from usual in: Sleep. "
                    "Other areas were not assessed by this check-in."
                ),
                "quick_check_in": {
                    "schema_version": 1,
                    "kind": "same_as_usual",
                    "selected_areas": ["sleep"],
                    "scope": "selected_areas_only",
                    "caregiver_reported_at": "2026-11-03T00:00:00+01:00",
                    "epistemic_status": "caregiver_reported",
                    "idempotency_key": "daily-checkin-retry-001",
                    "note": None,
                },
            },
        )
        assert response.status_code == 200, response.text
        assert response.json()["success"] is True

        # Simulate a check-in written by the earlier event-only implementation.
        # A safe retry must attach the missing source evidence without duplicating
        # the care event or losing the original report.
        db = TestingSessionLocal()
        legacy_event = db.query(CareEvent).filter_by(id=response.json()["id"]).one()
        legacy_event.evidence_ids = []
        db.query(Evidence).filter(Evidence.id.in_(response.json()["evidence_ids"])).delete(synchronize_session=False)
        db.commit()
        db.close()

        replay = client.post(
            "/api/v1/daily/log",
            headers=session_headers("person-daily-test", "caregiver-daily-test"),
            json=json.loads(response.request.content),
        )
        assert replay.status_code == 200, replay.text
        assert replay.json()["id"] == response.json()["id"]
        assert replay.json()["idempotent_replay"] is True
        assert len(replay.json()["evidence_ids"]) == 1

        db = TestingSessionLocal()
        assert db.query(CareEvent).filter_by(event_type="quick_daily_checkin").count() == 1
        assert db.query(Evidence).filter_by(origin_channel="daily_care_quick_checkin").count() == 1
        event = db.query(CareEvent).filter_by(id=response.json()["id"]).one()
        detail = json.loads(event.description)
        assert event.person_id == "person-daily-test"
        assert event.created_by_caregiver_id == "caregiver-daily-test"
        assert event.event_type == "quick_daily_checkin"
        assert event.title == "Quick daily check-in"
        assert detail["quick_check_in"]["kind"] == "same_as_usual"
        assert detail["quick_check_in"]["selected_areas"] == ["sleep"]
        assert detail["quick_check_in"]["scope"] == "selected_areas_only"
        assert detail["quick_check_in"]["epistemic_status"] == "caregiver_reported"
        assert "Other areas were not assessed" in detail["notes"]
        assert len(event.evidence_ids or []) == 1
        evidence = db.query(Evidence).filter_by(id=event.evidence_ids[0]).one()
        assert evidence.person_id == event.person_id
        assert evidence.uploaded_by_caregiver_id == "caregiver-daily-test"
        assert evidence.reported_by_caregiver_id == "caregiver-daily-test"
        assert evidence.epistemic_status == "caregiver_reported"
        assert evidence.source_type == "caregiver_report"
        assert evidence.origin_channel == "daily_care_quick_checkin"
        original_source = json.loads(evidence.source_text)
        assert "Other areas were not assessed" in original_source["original_notes"]
        assert original_source["quick_check_in"]["caregiver_reported_at"] == "2026-11-03T00:00:00+01:00"
        db.close()
    finally:
        teardown_db()



def test_quick_daily_checkin_rejects_unrecognized_or_unscoped_areas():
    setup_db()
    try:
        seed_person_and_caregiver()
        client = TestClient(app)
        response = client.post(
            "/api/v1/daily/log",
            headers=session_headers("person-daily-test", "caregiver-daily-test"),
            json={
                "person_id": "person-daily-test",
                "caregiver_id": "caregiver-daily-test",
                "medications": [],
                "vitals": [],
                "scales": [],
                "notes": "Caregiver quick update",
                "quick_check_in": {
                    "schema_version": 1,
                    "kind": "same_as_usual",
                    "selected_areas": ["diagnosis"],
                    "scope": "selected_areas_only",
                    "caregiver_reported_at": "2026-11-03T00:00:00+01:00",
                    "epistemic_status": "caregiver_reported",
                    "note": None,
                },
            },
        )
        assert response.status_code == 422, response.text
        assert response.json()["detail"] == "quick_check_in must contain valid, unique selected areas"

        db = TestingSessionLocal()
        assert db.query(CareEvent).filter_by(event_type="quick_daily_checkin").count() == 0
        db.close()
    finally:
        teardown_db()


def test_daily_capture_rejects_unknown_person():
    setup_db()
    try:
        seed_person_and_caregiver()
        client = TestClient(app)
        response = client.post(
            "/api/v1/daily-care/entries",
            headers=session_headers("person-daily-test", "caregiver-daily-test"),
            json={
                "person_id": "missing-person",
                "caregiver_id": "caregiver-daily-test",
                "mood": "Calm",
            },
        )
        assert response.status_code == 403
        assert response.json()["detail"] == "This session is not authorised for that care record"
    finally:
        teardown_db()


def test_daily_capture_rejects_unlinked_caregiver():
    setup_db()
    try:
        seed_person_and_caregiver()
        client = TestClient(app)
        response = client.post(
            "/api/v1/daily-care/entries",
            headers=session_headers("person-daily-test", "caregiver-daily-test"),
            json={
                "person_id": "person-daily-test",
                "caregiver_id": "not-linked-caregiver",
                "mood": "Calm",
            },
        )
        assert response.status_code == 403
        assert response.json()["detail"] == "Caregiver attribution does not match this session"
    finally:
        teardown_db()



def seed_second_person_and_caregiver():
    db = TestingSessionLocal()
    person = Person(id="person-other-test", name="Other Test Person")
    caregiver = Caregiver(
        id="caregiver-other-test",
        name="Other Test Caregiver",
        email="other-test-caregiver@example.invalid",
        relationship="Family",
        person_id=person.id,
    )
    db.add_all([person, caregiver])
    db.commit()
    db.close()


def test_event_write_rejects_caregiver_from_another_person():
    setup_db()
    try:
        seed_person_and_caregiver()
        seed_second_person_and_caregiver()
        client = TestClient(app)
        response = client.post(
            "/api/v1/events",
            json={
                "person_id": "person-daily-test",
                "event_type": "observation",
                "occurred_at": "2026-10-22T00:00:00Z",
                "title": "Ownership test",
                "created_by_caregiver_id": "caregiver-other-test",
            },
        )
        # Authentication is checked before caregiver membership so this
        # legacy write route never exposes record ownership to anonymous callers.
        assert response.status_code == 401, response.text
        assert response.json()["detail"] == "A valid SolenOS anonymous session is required"
    finally:
        teardown_db()


def test_appointment_write_rejects_caregiver_from_another_person():
    setup_db()
    try:
        seed_person_and_caregiver()
        seed_second_person_and_caregiver()
        client = TestClient(app)
        response = client.post(
            "/api/v1/appointments",
            json={
                "person_id": "person-daily-test",
                "title": "Ownership test appointment",
                "scheduled_at": "2026-10-23T10:00:00Z",
                "created_by_caregiver_id": "caregiver-other-test",
            },
        )
        assert response.status_code == 403, response.text
        assert response.json()["detail"] in {
            "Caregiver is not linked to this care record",
            "You are not a member of this care circle.",
        }
    finally:
        teardown_db()



def test_caregiver_creation_rejects_unknown_person():
    setup_db()
    try:
        client = TestClient(app)
        response = client.post(
            "/api/v1/caregivers",
            json={
                "name": "Unlinked Caregiver",
                "email": "unknown-person@example.invalid",
                "relationship": "Family",
                "person_id": "missing-person",
            },
        )
        # The authorization middleware may reject the unknown care-record ID
        # before the endpoint can return its more specific 404.
        assert response.status_code in {403, 404}, response.text
        assert response.json()["detail"] in {
            "Person not found",
            "You are not a member of this care circle.",
        }
    finally:
        teardown_db()


def test_insight_rejects_evidence_from_another_person():
    setup_db()
    try:
        seed_person_and_caregiver()
        seed_second_person_and_caregiver()
        db = TestingSessionLocal()
        foreign_evidence = Evidence(
            id="foreign-evidence-test",
            person_id="person-other-test",
            type="note",
            source_text="Evidence belongs to a different care record",
            uploaded_by_caregiver_id="caregiver-other-test",
        )
        db.add(foreign_evidence)
        db.commit()
        db.close()

        client = TestClient(app)
        headers = session_headers("person-daily-test", "caregiver-daily-test")
        response = client.post(
            "/api/v1/insights",
            headers=headers,
            json={
                "person_id": "person-daily-test",
                "title": "Cross-record evidence test",
                "description": "This should be rejected",
                "insight_type": "observation",
                "confidence": 0.5,
                "evidence_ids": ["foreign-evidence-test"],
            },
        )
        assert response.status_code == 422, response.text
        assert response.json()["detail"] == "Every evidence item must exist and belong to the same care record"
    finally:
        teardown_db()


def test_readiness_rejects_incomplete_schema():
    incomplete_engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.tables["persons"].create(bind=incomplete_engine)
    IncompleteSessionLocal = sessionmaker(
        autocommit=False, autoflush=False, bind=incomplete_engine
    )

    def override_incomplete_db():
        db = IncompleteSessionLocal()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_incomplete_db
    try:
        response = TestClient(app).get("/ready")
        assert response.status_code == 503, response.text
        assert response.json()["detail"] == "Database schema is incomplete"
    finally:
        app.dependency_overrides.clear()
        incomplete_engine.dispose()


def test_quick_daily_checkin_persists_uncertainty_without_inference():
    setup_db()
    try:
        seed_person_and_caregiver()
        client = TestClient(app)
        response = client.post(
            "/api/v1/daily/log",
            headers=session_headers("person-daily-test", "caregiver-daily-test"),
            json={
                "person_id": "person-daily-test",
                "caregiver_id": "caregiver-daily-test",
                "medications": [],
                "vitals": [],
                "scales": [],
                "notes": "Caregiver is unsure about: Sleep. No status is inferred for these areas.",
                "quick_check_in": {
                    "schema_version": 1,
                    "kind": "uncertain",
                    "selected_areas": ["sleep"],
                    "scope": "selected_areas_only",
                    "caregiver_reported_at": "2026-11-03T00:00:00+01:00",
                    "epistemic_status": "caregiver_reported",
                    "idempotency_key": "daily-checkin-uncertain-001",
                    "note": None,
                },
            },
        )
        assert response.status_code == 200, response.text
        db = TestingSessionLocal()
        event = db.query(CareEvent).filter_by(id=response.json()["id"]).one()
        detail = json.loads(event.description)
        assert detail["quick_check_in"]["kind"] == "uncertain"
        assert detail["quick_check_in"]["selected_areas"] == ["sleep"]
        assert "No status is inferred" in detail["notes"]
        db.close()
    finally:
        teardown_db()


def test_quick_daily_checkin_is_visible_as_caregiver_report_in_lcr_projection(monkeypatch):
    from app.api import care_continuity as care_continuity_api

    setup_db()
    db = TestingSessionLocal()
    try:
        report_time = "2026-11-03T00:00:00+01:00"
        event = {
            "id": "quick-checkin-lcr-event",
            "event_type": "quick_daily_checkin",
            "status": "recorded",
            "title": "Quick daily check-in",
            "description": json.dumps({
                "notes": "Caregiver reports no significant change in: Sleep.",
                "quick_check_in": {
                    "kind": "same_as_usual",
                    "selected_areas": ["sleep"],
                    "scope": "selected_areas_only",
                    "caregiver_reported_at": report_time,
                    "epistemic_status": "caregiver_reported",
                },
            }),
            "occurred_at": "2026-11-02T23:00:00+00:00",
            "recorded_at": "2026-11-02T23:00:01+00:00",
            "created_by_caregiver_id": "caregiver-daily-test",
            "evidence_ids": [],
        }
        monkeypatch.setattr(
            care_continuity_api,
            "_scoped_identity",
            lambda request, session: ("person-daily-test", None),
        )
        monkeypatch.setattr(
            care_continuity_api,
            "build_lcr_context",
            lambda session, person_id, limit_per_source: {
                "person_id": person_id,
                "as_of": report_time,
                "history_coverage": "bounded_subset",
                "schema_status": "complete",
                "missing_required_sources": [],
                "missing_optional_sources": [],
                "sources": {"events": {"items": [event]}},
            },
        )

        projection = care_continuity_api.current_care_projection(
            request=None, limit_per_source=10, db=db
        )
        item = next(
            item for item in projection["items"]
            if item["id"] == "quick-checkin-lcr-event"
        )
        assert item["source_collection"] == "events"
        assert item["kind"] == "Daily care entry"
        assert item["epistemic_status"] == "caregiver_reported"
        assert item["source_label"] == "Caregiver report"
        assert item["occurred_at"] == "2026-11-02T23:00:00+00:00"
        assert item["reported_at"] == report_time
    finally:
        db.close()
        teardown_db()


def test_same_as_usual_can_save_overall_impression_without_marking_domains_checked():
    setup_db()
    try:
        seed_person_and_caregiver()
        client = TestClient(app)
        response = client.post(
            "/api/v1/daily/log",
            headers=session_headers("person-daily-test", "caregiver-daily-test"),
            json={
                "person_id": "person-daily-test",
                "caregiver_id": "caregiver-daily-test",
                "medications": [],
                "vitals": [],
                "scales": [],
                "notes": (
                    "Caregiver reports no significant change in their overall impression of care. "
                    "No individual care areas were assessed by this check-in."
                ),
                "quick_check_in": {
                    "schema_version": 1,
                    "kind": "same_as_usual",
                    "selected_areas": [],
                    "scope": "overall_impression_only",
                    "caregiver_reported_at": "2026-11-03T00:00:00+01:00",
                    "epistemic_status": "caregiver_reported",
                    "idempotency_key": "daily-checkin-overall-001",
                    "note": None,
                },
            },
        )
        assert response.status_code == 200, response.text
        db = TestingSessionLocal()
        event = db.query(CareEvent).filter_by(id=response.json()["id"]).one()
        detail = json.loads(event.description)
        assert detail["quick_check_in"]["selected_areas"] == []
        assert detail["quick_check_in"]["scope"] == "overall_impression_only"
        assert "No individual care areas were assessed" in detail["notes"]
        db.close()
    finally:
        teardown_db()


def test_quick_daily_checkin_rejects_cross_recipient_session_scope():
    setup_db()
    try:
        seed_person_and_caregiver()
        seed_second_person_and_caregiver()
        client = TestClient(app)
        response = client.post(
            "/api/v1/daily/log",
            headers=session_headers("person-daily-test", "caregiver-daily-test"),
            json={
                "person_id": "person-other-test",
                "caregiver_id": "caregiver-other-test",
                "medications": [],
                "vitals": [],
                "scales": [],
                "notes": "Caregiver reports no significant change in their overall impression of care.",
                "quick_check_in": {
                    "schema_version": 1,
                    "kind": "same_as_usual",
                    "selected_areas": [],
                    "scope": "overall_impression_only",
                    "caregiver_reported_at": "2026-11-03T00:00:00+01:00",
                    "epistemic_status": "caregiver_reported",
                    "idempotency_key": "daily-checkin-cross-recipient-001",
                    "note": None,
                },
            },
        )
        assert response.status_code == 403, response.text
        assert response.json()["detail"] == "This session is not authorised for that care record"
        db = TestingSessionLocal()
        assert db.query(CareEvent).filter_by(event_type="quick_daily_checkin").count() == 0
        db.close()
    finally:
        teardown_db()

def test_legacy_event_write_requires_matching_server_session():
    setup_db()
    try:
        seed_person_and_caregiver()
        db = TestingSessionLocal()
        second_person = Person(id="person-daily-other", name="Other Person")
        second_caregiver = Caregiver(
            id="caregiver-daily-other",
            name="Other Caregiver",
            email="daily-other@example.invalid",
            relationship="Family",
            person_id=second_person.id,
        )
        db.add_all([second_person, second_caregiver])
        db.commit()
        db.close()

        client = TestClient(app)
        payload = {
            "person_id": "person-daily-test",
            "event_type": "observation",
            "occurred_at": datetime.now(timezone.utc).isoformat(),
            "title": "Unusual confusion",
            "description": "Mum was unusually confused this afternoon.",
            "created_by_caregiver_id": "caregiver-daily-test",
        }

        # The route previously accepted this write with no bearer token.
        unauthenticated = client.post("/api/v1/events", json=payload)
        assert unauthenticated.status_code == 401, unauthenticated.text

        # A valid token for another care record must not authorize this write.
        other_headers = session_headers("person-daily-other", "caregiver-daily-other")
        cross_person = client.post(
            "/api/v1/events",
            headers=other_headers,
            json=payload,
        )
        assert cross_person.status_code == 403, cross_person.text

        db = TestingSessionLocal()
        try:
            assert db.query(CareEvent).filter_by(person_id="person-daily-test").count() == 0
        finally:
            db.close()
    finally:
        teardown_db()

def test_care_event_correction_is_append_only_and_rejects_foreign_targets(monkeypatch):
    import app.api.v1 as api_v1

    setup_db()
    try:
        seed_person_and_caregiver()
        seed_second_person_and_caregiver()
        now = datetime.now(timezone.utc)
        original_id = "event-original-daily-test"
        foreign_id = "event-foreign-daily-test"
        db = TestingSessionLocal()
        original = CareEvent(
            id=original_id,
            person_id="person-daily-test",
            event_type="structured_daily_capture",
            status="recorded",
            occurred_at=now,
            title="Morning observation",
            description="Mum seemed unusually confused.",
            created_by_caregiver_id="caregiver-daily-test",
        )
        foreign = CareEvent(
            id=foreign_id,
            person_id="person-other-test",
            event_type="structured_daily_capture",
            status="recorded",
            occurred_at=now,
            title="Foreign observation",
            description="This belongs to another care record.",
            created_by_caregiver_id="caregiver-other-test",
        )
        db.add_all([original, foreign])
        db.commit()
        db.close()

        monkeypatch.setattr(
            api_v1,
            "execute_care_continuity_reconciliation",
            lambda *args, **kwargs: {
                "workflow": "care_record_correction",
                "status": "committed",
                "stages": {"reconciliation": "committed"},
            },
        )
        client = TestClient(app)
        headers = session_headers("person-daily-test", "caregiver-daily-test")
        response = client.post(
            "/api/v1/corrections",
            headers=headers,
            json={
                "person_id": "person-daily-test",
                "caregiver_id": "caregiver-daily-test",
                "target_type": "care_event",
                "target_id": original_id,
                "original_text": "Mum seemed unusually confused.",
                "corrected_text": "Mum was tired; confusion was not confirmed.",
                "reason": "Caregiver correction",
            },
        )
        assert response.status_code == 200, response.text
        correction_id = response.json()["id"]
        history = client.get(
            "/api/v1/daily-care/intelligence/history?limit=20&offset=0",
            headers=headers,
        )
        assert history.status_code == 200, history.text
        correction_entry = next(
            record for record in history.json()["records"]
            if record["source_type"] == "correction" and record["source_id"] == correction_id
        )
        assert correction_entry["target_type"] == "care_event"
        assert correction_entry["target_id"] == original_id

        db = TestingSessionLocal()
        try:
            unchanged = db.query(CareEvent).filter_by(id=original_id).one()
            assert unchanged.description == "Mum seemed unusually confused."
            link = db.query(CareRecordRelationship).filter_by(
                person_id="person-daily-test",
                source_type="correction",
                source_id=correction_id,
                relationship_type="corrects",
                target_type="care_event",
                target_id=original_id,
            ).one()
            assert "original care event retained unchanged" in link.provenance
        finally:
            db.close()

        foreign_response = client.post(
            "/api/v1/corrections",
            headers=headers,
            json={
                "person_id": "person-daily-test",
                "caregiver_id": "caregiver-daily-test",
                "target_type": "care_event",
                "target_id": foreign_id,
                "original_text": "Foreign original",
                "corrected_text": "Attempted foreign correction",
            },
        )
        assert foreign_response.status_code == 404, foreign_response.text
        db = TestingSessionLocal()
        try:
            assert db.query(Correction).count() == 1
        finally:
            db.close()
    finally:
        teardown_db()


def test_legacy_insight_and_correction_routes_require_scoped_sessions():
    setup_db()
    try:
        seed_person_and_caregiver()
        seed_second_person_and_caregiver()
        client = TestClient(app)
        insight = {
            "person_id": "person-daily-test",
            "title": "Reported change",
            "description": "Caregiver-reported change awaiting review.",
            "insight_type": "observation",
            "confidence": 0.5,
            "evidence_ids": ["not-used-before-auth"],
        }
        correction = {
            "person_id": "person-daily-test",
            "target_type": "care_event",
            "target_id": "event-not-used-before-auth",
            "original_text": "Original report",
            "corrected_text": "Corrected report",
            "caregiver_id": "caregiver-daily-test",
        }

        assert client.post("/api/v1/insights", json=insight).status_code == 401
        assert client.get("/api/v1/insights/person-daily-test").status_code == 401
        assert client.post("/api/v1/corrections", json=correction).status_code == 401
        assert client.get("/api/v1/corrections/person-daily-test").status_code == 401

        foreign_headers = session_headers("person-other-test", "caregiver-other-test")
        assert client.post(
            "/api/v1/insights", headers=foreign_headers, json=insight
        ).status_code == 403
        assert client.get(
            "/api/v1/insights/person-daily-test", headers=foreign_headers
        ).status_code == 403
        assert client.post(
            "/api/v1/corrections", headers=foreign_headers, json=correction
        ).status_code == 403
        assert client.get(
            "/api/v1/corrections/person-daily-test", headers=foreign_headers
        ).status_code == 403
    finally:
        teardown_db()

def test_legacy_observation_and_reasoning_routes_require_scoped_sessions():
    setup_db()
    try:
        seed_person_and_caregiver()
        seed_second_person_and_caregiver()
        client = TestClient(app)
        now = datetime.now(timezone.utc).isoformat()
        observation = {
            "person_id": "person-daily-test",
            "caregiver_id": "caregiver-daily-test",
            "observed_at": now,
            "original_text": "Caregiver reports a change in sleep.",
        }
        memory = {
            "person_id": "person-daily-test",
            "memory_type": "caregiver_report",
            "key": "sleep_change",
            "value": ["Caregiver reports a change in sleep."],
            "source_evidence_ids": [],
        }
        protected_requests = [
            ("post", "/api/v1/observations", observation),
            ("get", "/api/v1/observations/person-daily-test", None),
            ("get", "/api/v1/observations/person-daily-test/trends", None),
            ("post", "/api/v1/reasoning-memory", memory),
            ("get", "/api/v1/reasoning-memory/person-daily-test", None),
            ("get", "/api/v1/reasoning/person-daily-test/summary", None),
            ("get", "/api/v1/learning-events/person-daily-test", None),
        ]
        for method, path, payload in protected_requests:
            response = getattr(client, method)(path, json=payload) if payload is not None else getattr(client, method)(path)
            assert response.status_code == 401, (method, path, response.status_code, response.text)

        foreign_headers = session_headers("person-other-test", "caregiver-other-test")
        for method, path, payload in protected_requests:
            response = (
                getattr(client, method)(path, headers=foreign_headers, json=payload)
                if payload is not None
                else getattr(client, method)(path, headers=foreign_headers)
            )
            assert response.status_code == 403, (method, path, response.status_code, response.text)
    finally:
        teardown_db()

def test_legacy_person_and_caregiver_reads_require_scoped_sessions():
    setup_db()
    try:
        seed_person_and_caregiver()
        seed_second_person_and_caregiver()
        client = TestClient(app)
        protected_paths = [
            "/api/v1/persons/person-daily-test",
            "/api/v1/caregivers/person-daily-test",
        ]
        for path in protected_paths:
            assert client.get(path).status_code == 401
        foreign_headers = session_headers("person-other-test", "caregiver-other-test")
        for path in protected_paths:
            response = client.get(path, headers=foreign_headers)
            assert response.status_code == 403, (path, response.status_code, response.text)
        own_headers = session_headers("person-daily-test", "caregiver-daily-test")
        for path in protected_paths:
            response = client.get(path, headers=own_headers)
            assert response.status_code == 200, (path, response.status_code, response.text)
    finally:
        teardown_db()

def test_legacy_event_receipt_survives_secondary_pipeline_failures(monkeypatch):
    import app.api.v1 as api_v1

    setup_db()
    try:
        seed_person_and_caregiver()
        headers = session_headers("person-daily-test", "caregiver-daily-test")

        def fail_learning(*args, **kwargs):
            raise RuntimeError("injected learning pipeline failure")

        def fail_reconciliation(*args, **kwargs):
            raise RuntimeError("injected reconciliation failure")

        monkeypatch.setattr(api_v1, "_log_and_learn", fail_learning)
        monkeypatch.setattr(api_v1, "reconcile_person_change_intelligence", fail_reconciliation)

        client = TestClient(app)
        response = client.post(
            "/api/v1/events",
            headers=headers,
            json={
                "person_id": "person-daily-test",
                "event_type": "observation",
                "status": "recorded",
                "occurred_at": datetime.now(timezone.utc).isoformat(),
                "title": "Unusual confusion",
                "description": "Mum was unusually confused this afternoon. She also refused lunch.",
                "created_by_caregiver_id": "caregiver-daily-test",
            },
        )
        assert response.status_code == 200, response.text
        event_id = response.json()["id"]

        db = TestingSessionLocal()
        try:
            saved = db.query(CareEvent).filter_by(id=event_id).one()
            assert saved.description == "Mum was unusually confused this afternoon. She also refused lunch."
            assert saved.person_id == "person-daily-test"
            assert saved.created_by_caregiver_id == "caregiver-daily-test"
            assert db.query(CareEvent).filter_by(person_id="person-daily-test").count() == 1
        finally:
            db.close()
    finally:
        teardown_db()

def test_insight_receipt_survives_learning_pipeline_failure(monkeypatch):
    import app.api.v1 as api_v1

    setup_db()
    try:
        seed_person_and_caregiver()
        db = TestingSessionLocal()
        evidence = Evidence(
            id="insight-source-evidence",
            person_id="person-daily-test",
            type="note",
            source_text="Caregiver reported unusual confusion.",
            uploaded_by_caregiver_id="caregiver-daily-test",
        )
        db.add(evidence)
        db.commit()
        db.close()

        def fail_learning(*args, **kwargs):
            raise RuntimeError("injected learning failure")

        monkeypatch.setattr(api_v1, "_log_and_learn", fail_learning)
        response = TestClient(app).post(
            "/api/v1/insights",
            headers=session_headers("person-daily-test", "caregiver-daily-test"),
            json={
                "person_id": "person-daily-test",
                "title": "Reported confusion",
                "description": "Caregiver-reported observation.",
                "insight_type": "observation",
                "confidence": 0.5,
                "evidence_ids": ["insight-source-evidence"],
            },
        )
        assert response.status_code == 200, response.text
        db = TestingSessionLocal()
        try:
            saved = db.query(Insight).filter_by(id=response.json()["id"]).one()
            assert saved.person_id == "person-daily-test"
            assert saved.evidence_ids == ["insight-source-evidence"]
            assert db.query(Insight).count() == 1
        finally:
            db.close()
    finally:
        teardown_db()


def test_correction_receipt_survives_secondary_processing_failure(monkeypatch):
    import app.api.v1 as api_v1

    setup_db()
    try:
        seed_person_and_caregiver()
        from app.models.care import CareEvent
        target_db = TestingSessionLocal()
        try:
            target_db.add(CareEvent(
                id="event-source-1",
                person_id="person-daily-test",
                event_type="observation",
                status="recorded",
                occurred_at=datetime.now(timezone.utc),
                title="Caregiver observation",
                description="Mum was unusually confused.",
                created_by_caregiver_id="caregiver-daily-test",
            ))
            target_db.commit()
        finally:
            target_db.close()

        def no_op_apply(self, correction, *, commit=True):
            return None

        def fail_reconciliation(*args, **kwargs):
            raise RuntimeError("injected reconciliation failure")

        monkeypatch.setattr(api_v1.LearningEngine, "apply_correction", no_op_apply)
        monkeypatch.setattr(api_v1, "execute_care_continuity_reconciliation", fail_reconciliation)
        response = TestClient(app).post(
            "/api/v1/corrections",
            headers=session_headers("person-daily-test", "caregiver-daily-test"),
            json={
                "person_id": "person-daily-test",
                "target_type": "care_event",
                "target_id": "event-source-1",
                "original_text": "Mum was unusually confused.",
                "corrected_text": "Mum was tired; confusion was not confirmed.",
                "reason": "Caregiver correction",
                "caregiver_id": "caregiver-daily-test",
            },
        )
        assert response.status_code == 200, response.text
        execution = response.json()["care_continuity_execution"]
        assert execution["stages"] == {"correction": "committed", "reconciliation": "pending"}
        assert execution["retry_required"] is True
        db = TestingSessionLocal()
        try:
            saved = db.query(Correction).filter_by(id=response.json()["id"]).one()
            assert saved.person_id == "person-daily-test"
            assert saved.original_text == "Mum was unusually confused."
            assert saved.corrected_text == "Mum was tired; confusion was not confirmed."
            assert db.query(Correction).count() == 1
        finally:
            db.close()
    finally:
        teardown_db()



def test_insight_correction_preserves_original_and_appends_caregiver_history():
    setup_db()
    db = None
    try:
        seed_person_and_caregiver()
        db = TestingSessionLocal()
        original = Insight(
            id="insight-original-correction",
            person_id="person-daily-test",
            title="Possible sleep disruption",
            description="The record suggests sleep may be worsening.",
            insight_type="pattern",
            confidence=0.72,
            evidence_ids=[],
            is_active=True,
        )
        correction = Correction(
            id="correction-insight-append-only",
            person_id="person-daily-test",
            target_type="insight",
            target_id=original.id,
            original_text=original.description,
            corrected_text="The caregiver says sleep was not worsening.",
            reason="The earlier interpretation did not reflect the caregiver's account.",
            caregiver_id="caregiver-daily-test",
        )
        db.add_all([original, correction])
        db.commit()

        LearningEngine(db).apply_correction(correction)
        db.expire_all()

        saved = db.query(Insight).filter_by(id="insight-original-correction").one()
        assert saved.description == "The record suggests sleep may be worsening."
        assert saved.is_active is False
        link = db.query(CareRecordRelationship).filter_by(
            person_id="person-daily-test",
            source_type="correction",
            source_id="correction-insight-append-only",
            relationship_type="corrects",
            target_type="insight",
            target_id="insight-original-correction",
        ).one()
        assert "original insight retained unchanged" in link.provenance
        memory = db.query(ReasoningMemory).filter_by(
            person_id="person-daily-test",
            memory_type="correction_history",
            key="insight_insight-original-correction",
        ).one()
        assert memory.value[0]["corrected"] == "The caregiver says sleep was not worsening."
        assert memory.value[0]["caregiver_id"] == "caregiver-daily-test"
        assert memory.value[0]["information_status"] == "caregiver_reported_correction"
    finally:
        if db is not None:
            db.close()
        teardown_db()


def test_quick_checkin_accepts_three_overall_impressions_without_domain_assessment():
    setup_db()
    try:
        seed_person_and_caregiver()
        client = TestClient(app)
        headers = session_headers("person-daily-test", "caregiver-daily-test")
        for index, kind in enumerate(("same_as_usual", "change", "uncertain")):
            response = client.post(
                "/api/v1/daily/log",
                headers=headers,
                json={
                    "person_id": "person-daily-test",
                    "caregiver_id": "caregiver-daily-test",
                    "medications": [],
                    "vitals": [],
                    "scales": [],
                    "notes": "",
                    "quick_check_in": {
                        "schema_version": 1,
                        "kind": kind,
                        "selected_areas": [],
                        "scope": "overall_impression_only",
                        "caregiver_reported_at": "2026-11-12T10:00:00+00:00",
                        "epistemic_status": "caregiver_reported",
                        "idempotency_key": f"overall-checkin-{index:03d}",
                        "note": None,
                    },
                },
            )
            assert response.status_code == 200, response.text
            assert response.json()["success"] is True
            assert response.json()["event_type"] == "quick_daily_checkin"
    finally:
        teardown_db()


def test_daily_capture_preserves_explicit_event_time_and_server_recorded_time_separately():
    setup_db()
    try:
        seed_person_and_caregiver()
        client = TestClient(app)
        response = client.post(
            "/api/v1/daily/log",
            headers=session_headers("person-daily-test", "caregiver-daily-test"),
            json={
                "person_id": "person-daily-test",
                "caregiver_id": "caregiver-daily-test",
                "notes": "Caregiver is recording an event from earlier.",
                "occurred_at": "2026-11-11T18:45:00+01:00",
                "capture_idempotency_key": "explicit-event-time-001",
            },
        )
        assert response.status_code == 200, response.text
        receipt = response.json()
        assert receipt["success"] is True

        db = TestingSessionLocal()
        try:
            event = db.query(CareEvent).filter_by(id=receipt["id"]).one()
            evidence = db.query(Evidence).filter_by(id=receipt["evidence_ids"][0]).one()
            expected_utc = datetime(2026, 11, 11, 17, 45, tzinfo=timezone.utc)
            event_time = event.occurred_at
            evidence_time = evidence.occurred_at
            if event_time.tzinfo is None:
                event_time = event_time.replace(tzinfo=timezone.utc)
            if evidence_time.tzinfo is None:
                evidence_time = evidence_time.replace(tzinfo=timezone.utc)
            assert event_time == expected_utc
            assert evidence_time == expected_utc
            assert event.time_provenance == "caregiver_reported_at"
            assert evidence.time_provenance == "caregiver_reported_at"
            assert event.recorded_at is not None
        finally:
            db.close()
    finally:
        teardown_db()



def test_medication_authoring_starts_reported_and_requires_explicit_review():
    from app.models.care_continuity import CareAssertion

    setup_db()
    try:
        seed_person_and_caregiver()
        client = TestClient(app)
        headers = session_headers("person-daily-test", "caregiver-daily-test")

        created = client.post(
            "/api/v1/care-continuity/medications",
            headers=headers,
            json={"name": "Metformin", "dose": "500 mg", "note": "Listed on the discharge papers", "review_after_days": 30},
        )
        assert created.status_code == 201, created.text
        report = created.json()
        assert report["record_status"] == "awaiting_caregiver_review"
        assert report["name"] == "Metformin"
        assert len(report["assertions"]) == 3
        assert {row["assertion_status"] for row in report["assertions"]} == {"reported"}

        projection = client.get("/api/v1/care-continuity/medications", headers=headers)
        assert projection.status_code == 200, projection.text
        assert projection.json()["medications"] == []
        assert len(projection.json()["pending_review"]) == 1
        assert projection.json()["pending_review"][0]["medication_id"] == report["medication_id"]

        # Simulate a report that has remained unreviewed beyond its original
        # review window. It must stay visible for review, not silently disappear.
        db = TestingSessionLocal()
        stale_rows = db.query(CareAssertion).filter_by(
            person_id="person-daily-test",
            subject_type="medication",
            subject_id=report["medication_id"],
        ).all()
        for row in stale_rows:
            row.recorded_at = datetime.now(timezone.utc) - timedelta(days=60)
        db.commit()
        db.close()
        with engine.begin() as connection:
            connection.exec_driver_sql("""
                CREATE TABLE care_assertion_revisions (
                    id VARCHAR(36) PRIMARY KEY,
                    person_id VARCHAR(36) NOT NULL,
                    assertion_id VARCHAR(36) NOT NULL,
                    revision_number INTEGER NOT NULL,
                    previous_payload JSON,
                    current_payload JSON,
                    revision_reason TEXT,
                    provenance_id VARCHAR(36),
                    changed_by_caregiver_id VARCHAR(36),
                    changed_at DATETIME NOT NULL
                )
            """)

        stale_projection = client.get("/api/v1/care-continuity/medications", headers=headers)
        assert stale_projection.status_code == 200, stale_projection.text
        assert len(stale_projection.json()["pending_review"]) == 1
        assert stale_projection.json()["pending_review"][0]["review_overdue"] is True

        reviewed = client.post(
            f"/api/v1/care-continuity/medications/{report['medication_id']}/review",
            headers=headers,
            json={},
        )
        assert reviewed.status_code == 200, reviewed.text
        assert reviewed.json()["record_status"] == "caregiver_reviewed_current_report"
        assert reviewed.json()["care_continuity_execution"]["stages"]["reconciliation"] == "committed"
        assert len(reviewed.json()["assertions"]) == 3
        assert {row["assertion_status"] for row in reviewed.json()["assertions"]} == {"accepted"}

        current = client.get("/api/v1/care-continuity/medications", headers=headers)
        assert current.status_code == 200, current.text
        assert current.json()["count"] == 1
        assert current.json()["medications"][0]["name"] == "Metformin"
        assert current.json()["medications"][0]["dose"] == "500 mg"
        assert current.json()["pending_review"] == []

        db = TestingSessionLocal()
        try:
            stored = db.query(CareAssertion).filter_by(
                person_id="person-daily-test",
                subject_type="medication",
                subject_id=report["medication_id"],
            ).all()
            assert len(stored) == 3
            assert all(row.source_label == "Caregiver report" for row in stored)
            assert all(row.created_by_caregiver_id == "caregiver-daily-test" for row in stored)
        finally:
            db.close()
    finally:
        with engine.begin() as connection:
            connection.exec_driver_sql("DROP TABLE IF EXISTS care_assertion_revisions")
        teardown_db()


def test_medication_authoring_and_review_are_person_scoped():
    setup_db()
    try:
        seed_person_and_caregiver()
        db = TestingSessionLocal()
        foreign_person = Person(id="person-medication-other", name="Other Person")
        foreign_caregiver = Caregiver(
            id="caregiver-medication-other",
            name="Other Caregiver",
            email="medication-other@example.invalid",
            relationship="Family",
            person_id=foreign_person.id,
        )
        db.add_all([foreign_person, foreign_caregiver])
        db.commit()
        db.close()

        client = TestClient(app)
        owner_headers = session_headers("person-daily-test", "caregiver-daily-test")
        foreign_headers = session_headers("person-medication-other", "caregiver-medication-other")
        created = client.post(
            "/api/v1/care-continuity/medications",
            headers=owner_headers,
            json={"name": "Private medicine", "review_after_days": 30},
        )
        assert created.status_code == 201, created.text
        medication_id = created.json()["medication_id"]

        foreign_review = client.post(
            f"/api/v1/care-continuity/medications/{medication_id}/review",
            headers=foreign_headers,
            json={},
        )
        assert foreign_review.status_code == 404
        foreign_projection = client.get("/api/v1/care-continuity/medications", headers=foreign_headers)
        assert foreign_projection.status_code == 200
        assert foreign_projection.json()["medications"] == []
        assert foreign_projection.json()["pending_review"] == []
    finally:
        teardown_db()


def test_current_medication_projection_uses_only_reviewed_unambiguous_lcr_assertions():
    from app.models.care_continuity import CareAssertion

    setup_db()
    try:
        seed_person_and_caregiver()
        db = TestingSessionLocal()
        foreign_person = Person(id="person-medication-foreign", name="Foreign Person")
        foreign_person_id = foreign_person.id
        foreign_caregiver = Caregiver(
            id="caregiver-medication-foreign",
            name="Foreign Caregiver",
            email="medication-foreign@example.invalid",
            relationship="Family",
            person_id=foreign_person_id,
        )
        db.add_all([foreign_person, foreign_caregiver])
        db.commit()
        db.close()

        def claim(subject_id, field, value, *, person_id="person-daily-test", status="accepted", age_days=2, epistemic_state="reported"):
            return CareAssertion(
                person_id=person_id,
                subject_type="medication",
                subject_id=subject_id,
                field=field,
                assertion_kind="current_state",
                asserted_value=value,
                source_label="Caregiver report",
                source_text="Recorded by caregiver",
                assertion_status=status,
                epistemic_state=epistemic_state,
                review_after_days=30,
                recorded_at=datetime.now(timezone.utc) - timedelta(days=age_days),
            )

        db = TestingSessionLocal()
        db.add_all([
            claim("med-valid", "medication_name", "Metformin"),
            claim("med-valid", "medication_status", "active"),
            claim("med-valid", "dose", "500 mg"),
            claim("med-expired", "medication_name", "Old medicine", age_days=45),
            claim("med-expired", "medication_status", "active", age_days=45),
            claim("med-unreviewed", "medication_name", "Reported only", status="reported"),
            claim("med-unreviewed", "medication_status", "active", status="reported"),
            claim("med-conflict", "medication_name", "Aspirin"),
            claim("med-conflict", "medication_name", "Warfarin"),
            claim("med-conflict", "medication_status", "active"),
            claim("med-inferred", "medication_name", "Inferred medicine", epistemic_state="inferred"),
            claim("med-inferred", "medication_status", "active", epistemic_state="inferred"),
            claim("med-foreign", "medication_name", "Private medicine", person_id=foreign_person_id),
            claim("med-foreign", "medication_status", "active", person_id=foreign_person_id),
        ])
        db.commit()
        db.close()

        client = TestClient(app)
        headers = session_headers("person-daily-test", "caregiver-daily-test")
        response = client.get("/api/v1/care-continuity/medications", headers=headers)
        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["source"] == "care_assertions"
        assert payload["history_events_promoted"] is False
        assert payload["count"] == 1
        assert payload["medications"][0]["medication_id"] == "med-valid"
        assert payload["medications"][0]["name"] == "Metformin"
        assert payload["medications"][0]["dose"] == "500 mg"
        assert payload["medications"][0]["record_status"] == "caregiver_reviewed_current_report"
        assert "not independent clinical verification" in payload["medications"][0]["interpretation_limit"]
    finally:
        teardown_db()
