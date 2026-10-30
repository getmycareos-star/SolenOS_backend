from datetime import datetime, timezone

import pytest
from pydantic import ValidationError
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.main as main_module
from app.core.database import Base, SessionLocal as DefaultSessionLocal
from app.main import app
from app.models.care import Person, Caregiver, Evidence, CareEvent
from app.models.care_continuity import EvidenceObservation
from app.models.change import Situation
from app.models.uncertainty import OpenQuestion, Contradiction
from app.core.change_enums import SituationType, SituationState
from app.schemas.situation import SituationInput
from app.services.situation import process_situation_input

SQLALCHEMY_DATABASE_URL = "sqlite:///:memory:"

engine = create_engine(
    SQLALCHEMY_DATABASE_URL,
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


app.dependency_overrides = {}


def setup_db():
    Base.metadata.create_all(bind=engine)
    main_module.SessionLocal = TestingSessionLocal


def teardown_db():
    Base.metadata.drop_all(bind=engine)
    app.dependency_overrides.clear()
    main_module.SessionLocal = DefaultSessionLocal


def create_authenticated_session(client, person_name: str):
    response = client.post(
        "/api/v1/sessions/anonymous",
        json={"name": person_name, "caregiver_name": "Test family caregiver"},
    )
    assert response.status_code == 201, response.text
    body = response.json()
    return body, {"Authorization": f"Bearer {body['access_token']}"}


@pytest.mark.session_security
def test_post_situation_creates_evidence_and_event():
    setup_db()
    try:
        from app.core.database import get_db
        app.dependency_overrides[get_db] = override_get_db

        client = TestClient(app)
        created, headers = create_authenticated_session(client, "Test Person")
        person_id = created["person"]["id"]
        caregiver_id = created["caregiver"]["id"]
        response = client.post(
            "/api/situation",
            headers=headers,
            json={
                "caregiver_id": caregiver_id,
                "person_id": person_id,
                "raw_input": "Patient seemed more confused than usual today.",
            },
        )
        assert response.status_code == 200, response.text
        data = response.json()
        assert data["ok"] is True
        assert data["care_key"] == caregiver_id
        assert data["person_id"] == person_id
        assert data["message"] == "Care input recorded successfully."
        assert data["analysis_status"] == "complete"
        assert data["analysis_warning"] is None
        assert len(data["events"]) == 1
        assert data["events"][0]["title"] == "Patient seemed more confused than usual today."
        assert data["events"][0]["time_provenance"] == "entry_time_defaulted"
        assert data["evidence_id"] is not None
        assert data["care_event_id"] is not None
    finally:
        teardown_db()



@pytest.mark.session_security
def test_multi_speaker_capture_persists_exact_speaker_and_reporter_provenance():
    setup_db()
    try:
        from app.core.database import get_db
        app.dependency_overrides[get_db] = override_get_db
        client = TestClient(app)
        created, headers = create_authenticated_session(client, "Speaker Provenance Person")
        person_id = created["person"]["id"]
        caregiver_id = created["caregiver"]["id"]
        source = (
            "[2026-10-30T09:15:00Z] Daughter: Mom fell in the hallway this morning.\n"
            "[Nurse]: She was assessed at urgent care.\n"
            "Daughter: She is afraid to walk to the kitchen now."
        )
        response = client.post(
            "/api/situation",
            headers=headers,
            json={
                "caregiver_id": caregiver_id,
                "person_id": person_id,
                "raw_input": source,
            },
        )
        assert response.status_code == 200, response.text
        evidence_id = response.json()["evidence_id"]
        with TestingSessionLocal() as db:
            evidence = db.query(Evidence).filter_by(id=evidence_id, person_id=person_id).one()
            rows = (
                db.query(EvidenceObservation)
                .filter_by(evidence_id=evidence_id, person_id=person_id)
                .order_by(EvidenceObservation.source_span_start.asc())
                .all()
            )
            assert evidence.source_text == source
            assert [row.observer_label for row in rows] == ["Daughter", "Nurse", "Daughter"]
            assert [row.epistemic_status for row in rows] == ["reported", "reported", "reported"]
            assert all(row.observation_kind == "imported_information" for row in rows)
            assert all(row.created_by_caregiver_id == caregiver_id for row in rows)
            assert all(row.observer_caregiver_id is None for row in rows)
            assert all(
                evidence.source_text[row.source_span_start:row.source_span_end] == row.source_passage
                for row in rows
            )
            assert [row.source_passage for row in rows] == [
                "Mom fell in the hallway this morning.",
                "She was assessed at urgent care.",
                "She is afraid to walk to the kitchen now.",
            ]

        listed = client.get(
            f"/api/v1/care-continuity/evidence/{evidence_id}/observations",
            headers=headers,
        )
        assert listed.status_code == 200, listed.text
        listed_rows = listed.json()
        assert len(listed_rows) == 3
        assert [row["observer_label"] for row in listed_rows] == ["Daughter", "Nurse", "Daughter"]

        original_statement = listed_rows[0]
        correction = client.post(
            f"/api/v1/care-continuity/evidence/{evidence_id}/observations",
            headers=headers,
            json={
                "observation_kind": "caregiver_report",
                "epistemic_status": "reported",
                "subject_label": original_statement["subject_label"],
                "attribute": "reported_statement",
                "value_text": "Mom slipped in the hallway this morning.",
                "context": "Caregiver correction to the structured interpretation; original source wording remains unchanged.",
                "observer_label": "Daughter",
                "source_passage": original_statement["source_passage"],
                "source_span_start": original_statement["source_span_start"],
                "source_span_end": original_statement["source_span_end"],
                "extraction_method": "caregiver_correction",
                "extraction_version": "1",
                "reported_at": datetime.now(timezone.utc).isoformat(),
                "supersedes_observation_id": original_statement["id"],
                "correction_rationale": "Caregiver clarified the structured wording after reviewing the original report.",
            },
        )
        assert correction.status_code == 201, correction.text
        correction_id = correction.json()["id"]

        # A later source can correct a prior statement. The source being
        # corrected remains immutable and both source records remain auditable.
        later_source_text = "Daughter: I reviewed the discharge note; Mom slipped rather than fell."
        later_source = client.post(
            "/api/v1/evidence",
            json={
                "person_id": person_id,
                "uploaded_by_caregiver_id": caregiver_id,
                "type": "note",
                "evidence_kind": "caregiver_report",
                "epistemic_status": "unverified",
                "source_text": later_source_text,
            },
            headers=headers,
        )
        assert later_source.status_code in {200, 201}, later_source.text
        later_evidence_id = later_source.json()["id"]
        later_passage = "Mom slipped rather than fell."
        later_start = later_source_text.index(later_passage)
        cross_source_correction = client.post(
            f"/api/v1/care-continuity/evidence/{later_evidence_id}/observations",
            headers=headers,
            json={
                "observation_kind": "caregiver_report",
                "epistemic_status": "reported",
                "subject_label": original_statement["subject_label"],
                "attribute": original_statement["attribute"],
                "value_text": "Mom slipped in the hallway this morning.",
                "context": "A later caregiver report corrects the earlier wording after reviewing the discharge note.",
                "observer_label": "Daughter",
                "source_passage": later_passage,
                "source_span_start": later_start,
                "source_span_end": later_start + len(later_passage),
                "extraction_method": "caregiver_correction",
                "extraction_version": "1",
                "reported_at": datetime.now(timezone.utc).isoformat(),
                "supersedes_observation_id": correction_id,
                "correction_rationale": "Later source clarifies the earlier structured statement.",
            },
        )
        assert cross_source_correction.status_code == 201, cross_source_correction.text
        cross_source_correction_id = cross_source_correction.json()["id"]

        hydrated = client.get("/api/situation", headers=headers)
        assert hydrated.status_code == 200, hydrated.text
        source_thread = next(
            item for item in hydrated.json()["ui_situations"]
            if evidence_id in item.get("linked_evidence_ids", [])
        )
        source_statements = source_thread["sourceStatements"]
        original_statement_ids = {row["id"] for row in listed_rows}
        assert [
            item["speaker_label"] for item in source_statements
            if item["id"] in original_statement_ids
        ] == ["Daughter", "Nurse", "Daughter"]
        assert all(item["reporter_caregiver_id"] == caregiver_id for item in source_statements)
        assert all(item["speaker_attribution_status"] == "explicit" for item in source_statements[:3])
        original_after_correction = next(item for item in source_statements if item["id"] == original_statement["id"])
        nurse_statement = next(item for item in source_statements if item["speaker_label"] == "Nurse")
        assert original_after_correction["occurred_at"] is not None
        assert nurse_statement["occurred_at"] is None
        correction_after_hydration = next(item for item in source_statements if item["id"] == correction_id)
        assert original_after_correction["superseded_by_observation_ids"] == [correction_id]
        assert correction_after_hydration["supersedes_observation_id"] == original_statement["id"]
        assert correction_after_hydration["correction_rationale"]
        assert correction_after_hydration["superseded_by_observation_ids"] == [cross_source_correction_id]
        later_observations = client.get(
            f"/api/v1/care-continuity/evidence/{later_evidence_id}/observations",
            headers=headers,
        )
        assert later_observations.status_code == 200, later_observations.text
        cross_source_row = next(
            item for item in later_observations.json()
            if item["id"] == cross_source_correction_id
        )
        assert cross_source_row["evidence_id"] == later_evidence_id
        assert cross_source_row["supersedes_observation_id"] == correction_id
        assert cross_source_row["source_passage"] == later_passage
        assert all(
            source[item["source_span_start"]:item["source_span_end"]] == item["source_passage"]
            for item in source_statements
        )
    finally:
        teardown_db()


@pytest.mark.session_security
def test_get_situation_rehydrates_distinct_persisted_threads_and_open_questions():
    setup_db()
    try:
        from app.core.database import get_db
        app.dependency_overrides[get_db] = override_get_db
        client = TestClient(app)
        created, headers = create_authenticated_session(client, "Distinct Thread Hydration")
        person_id = created["person"]["id"]
        caregiver_id = created["caregiver"]["id"]

        first = client.post(
            "/api/situation",
            headers=headers,
            json={
                "caregiver_id": caregiver_id,
                "person_id": person_id,
                "raw_input": "The person has been unsteady when walking.",
            },
        )
        second = client.post(
            "/api/situation",
            headers=headers,
            json={
                "caregiver_id": caregiver_id,
                "person_id": person_id,
                "raw_input": "The person has been eating less at dinner.",
            },
        )
        assert first.status_code == 200, first.text
        assert second.status_code == 200, second.text

        with TestingSessionLocal() as db:
            first_event = db.query(CareEvent).filter_by(id=first.json()["care_event_id"]).one()
            second_event = db.query(CareEvent).filter_by(id=second.json()["care_event_id"]).one()
            first_evidence_id = first.json()["evidence_id"]
            second_evidence_id = second.json()["evidence_id"]
            first_situation = Situation(
                id="persisted-mobility-thread",
                person_id=person_id,
                situation_type=SituationType.UNKNOWN,
                state=SituationState.ACTIVE,
                title="Walking stability",
                description="A separately persisted mobility concern.",
                signal_ids=[first_evidence_id],
                signal_types=["mobility"],
                temporal_start=first_event.occurred_at,
                temporal_end=first_event.occurred_at,
            )
            second_situation = Situation(
                id="persisted-appetite-thread",
                person_id=person_id,
                situation_type=SituationType.UNKNOWN,
                state=SituationState.ACTIVE,
                title="Eating pattern",
                description="A separately persisted eating concern.",
                signal_ids=[second_evidence_id],
                signal_types=["appetite"],
                temporal_start=second_event.occurred_at,
                temporal_end=second_event.occurred_at,
            )
            first_event.situation_id = first_situation.id
            second_event.situation_id = second_situation.id
            db.add_all([
                first_situation,
                second_situation,
                OpenQuestion(
                    id="open-mobility-question",
                    person_id=person_id,
                    question="Has the unsteadiness also happened indoors?",
                    subject_type="mobility",
                    subject_id=person_id,
                    status="open",
                ),
                Contradiction(
                    id="unresolved-mobility-conflict",
                    person_id=person_id,
                    subject_type="mobility",
                    subject_id=person_id,
                    field="walking_stability",
                    description="Daughter reports unsteadiness; another account says walking was steady.",
                    resolution_status="unresolved",
                    evidence_ids=[first_evidence_id],
                ),
            ])
            db.commit()

        related_link = client.post(
            "/api/v1/care-continuity/relationships",
            headers=headers,
            json={
                "source_type": "situation",
                "source_id": "persisted-mobility-thread",
                "relationship_type": "related_to",
                "target_type": "situation",
                "target_id": "persisted-appetite-thread",
                "provenance": "Caregiver linked these as related concerns after the recent care transition; they remain separate threads.",
            },
        )
        assert related_link.status_code == 201, related_link.text
        duplicate_related_link = client.post(
            "/api/v1/care-continuity/relationships",
            headers=headers,
            json={
                "source_type": "situation",
                "source_id": "persisted-appetite-thread",
                "relationship_type": "related_to",
                "target_type": "situation",
                "target_id": "persisted-mobility-thread",
                "provenance": "Caregiver repeated the same related concern link in reverse order.",
            },
        )
        assert duplicate_related_link.status_code == 409, duplicate_related_link.text
        causal_link = client.post(
            "/api/v1/care-continuity/relationships",
            headers=headers,
            json={
                "source_type": "situation",
                "source_id": "persisted-mobility-thread",
                "relationship_type": "causes",
                "target_type": "situation",
                "target_id": "persisted-appetite-thread",
                "provenance": "No causal evidence is available for this test.",
            },
        )
        assert causal_link.status_code == 422, causal_link.text
        retract_link = client.post(
            "/api/v1/care-continuity/relationships",
            headers=headers,
            json={
                "source_type": "situation",
                "source_id": "persisted-appetite-thread",
                "relationship_type": "unrelated_to",
                "target_type": "situation",
                "target_id": "persisted-mobility-thread",
                "provenance": "Caregiver corrected the prior relationship and now considers these concerns unrelated.",
            },
        )
        assert retract_link.status_code == 201, retract_link.text
        after_retraction = client.get("/api/situation", headers=headers)
        assert after_retraction.status_code == 200, after_retraction.text
        retracted_threads = {
            item["id"]: item for item in after_retraction.json()["ui_situations"]
        }
        assert retracted_threads["persisted-mobility-thread"]["relatedSituations"] == []
        assert retracted_threads["persisted-appetite-thread"]["relatedSituations"] == []
        restore_link = client.post(
            "/api/v1/care-continuity/relationships",
            headers=headers,
            json={
                "source_type": "situation",
                "source_id": "persisted-mobility-thread",
                "relationship_type": "related_to",
                "target_type": "situation",
                "target_id": "persisted-appetite-thread",
                "provenance": "Caregiver restored the explicit non-causal relationship after review.",
            },
        )
        assert restore_link.status_code == 201, restore_link.text

        # Explicitly resume the mobility thread with a new observation. The
        # appetite thread must remain separate and retain its own evidence.
        resumed = client.post(
            "/api/situation",
            headers=headers,
            json={
                "caregiver_id": caregiver_id,
                "person_id": person_id,
                "target_situation_id": "persisted-mobility-thread",
                "raw_input": "The person is still unsteady when turning.",
            },
        )
        assert resumed.status_code == 200, resumed.text
        resumed_body = resumed.json()
        assert resumed_body["situation_id"] == "persisted-mobility-thread"
        resumed_evidence_id = resumed_body["evidence_id"]
        with TestingSessionLocal() as db:
            resumed_event = db.query(CareEvent).filter_by(
                id=resumed_body["care_event_id"]
            ).one()
            saved_mobility = db.query(Situation).filter_by(
                id="persisted-mobility-thread"
            ).one()
            assert resumed_event.situation_id == "persisted-mobility-thread"
            assert resumed_evidence_id in saved_mobility.signal_ids

        hydrated = client.get("/api/situation", headers=headers)
        assert hydrated.status_code == 200, hydrated.text
        body = hydrated.json()
        threads = {item["id"]: item for item in body["ui_situations"]}
        assert "persisted-mobility-thread" in threads
        assert "persisted-appetite-thread" in threads
        assert set(threads["persisted-mobility-thread"]["linked_evidence_ids"]) == {
            first_evidence_id, resumed_evidence_id
        }
        assert threads["persisted-appetite-thread"]["linked_evidence_ids"] == [second_evidence_id]
        assert threads["persisted-mobility-thread"]["openQuestions"] == [
            "Has the unsteadiness also happened indoors?"
        ]
        assert threads["persisted-mobility-thread"]["conflicts"][0]["winner_selected"] is False
        assert threads["persisted-mobility-thread"]["conflicts"][0]["resolution_status"] == "unresolved"
        assert threads["persisted-appetite-thread"]["openQuestions"] == []
        assert threads["persisted-mobility-thread"]["relatedSituations"][0]["id"] == "persisted-appetite-thread"
        assert threads["persisted-appetite-thread"]["relatedSituations"][0]["id"] == "persisted-mobility-thread"
        assert set(threads["persisted-mobility-thread"]["linked_evidence_ids"]).isdisjoint(
            threads["persisted-appetite-thread"]["linked_evidence_ids"]
        )
        assert "Has the unsteadiness also happened indoors?" in body["return_continuity"]["important_unresolved"]
        assert any(
            item.startswith("Conflicting reports:")
            for item in body["return_continuity"]["important_unresolved"]
        )
    finally:
        teardown_db()


@pytest.mark.session_security
def test_post_situation_preserves_caregiver_reported_event_time_provenance():
    setup_db()
    try:
        from app.core.database import get_db
        app.dependency_overrides[get_db] = override_get_db

        client = TestClient(app)
        created, headers = create_authenticated_session(client, "Reported Event Time Person")
        event_time = "2026-10-20T08:30:00+00:00"
        response = client.post(
            "/api/situation",
            headers=headers,
            json={
                "caregiver_id": created["caregiver"]["id"],
                "person_id": created["person"]["id"],
                "raw_input": "Caregiver reports restless sleep.",
                "timestamp": event_time,
            },
        )
        assert response.status_code == 200, response.text
        data = response.json()
        assert data["events"][0]["occurred_at"].startswith("2026-10-20T08:30:00")
        assert data["events"][0]["time_provenance"] == "caregiver_reported_event_time"

        db = TestingSessionLocal()
        try:
            saved = db.query(CareEvent).filter_by(id=data["care_event_id"]).one()
            assert saved.time_provenance == "caregiver_reported_event_time"
            assert saved.occurred_at.isoformat().startswith("2026-10-20T08:30:00")
        finally:
            db.close()
    finally:
        teardown_db()


@pytest.mark.session_security
def test_post_situation_with_documents():
    setup_db()
    try:
        from app.core.database import get_db
        app.dependency_overrides[get_db] = override_get_db

        client = TestClient(app)
        created, headers = create_authenticated_session(client, "Test Person 2")
        response = client.post(
            "/api/situation",
            headers=headers,
            json={
                "caregiver_id": created["caregiver"]["id"],
                "person_id": created["person"]["id"],
                "raw_input": "",
                "documents": [
                    {
                        "id": "doc-1",
                        "name": "discharge_summary.pdf",
                        "extracted_text": "Patient cleared for discharge with follow-up in 1 week.",
                        "mime_type": "application/pdf",
                        "ocr_confidence": 0.95,
                    }
                ],
                "provenance": {
                    "input_type": "document",
                    "entry_method": "upload",
                    "captured_at": datetime.now(timezone.utc).isoformat(),
                    "recognition_confidence": 0.95,
                    "transcript_uncertain": False,
                },
            },
        )
        assert response.status_code == 200, response.text
        data = response.json()
        assert data["ok"] is True
        assert len(data["events"]) == 1
        assert data["events"][0]["title"] == "Document observation"
    finally:
        teardown_db()


def test_situation_service_rejects_missing_caregiver():
    setup_db()
    try:
        db = TestingSessionLocal()
        db.add(Person(id="person-1", name="Test Person"))
        db.commit()
        with pytest.raises(ValueError, match="Caregiver not found"):
            process_situation_input(
                db,
                SituationInput(
                    caregiver_id="nonexistent",
                    person_id="person-1",
                    raw_input="Some input",
                ),
            )
        db.close()
    finally:
        teardown_db()


def test_situation_service_rejects_missing_person():
    setup_db()
    try:
        db = TestingSessionLocal()
        caregiver = Caregiver(
            id="caregiver-3",
            name="Test Caregiver 3",
            email="test3@example.com",
            relationship="Family",
            person_id="person-3",
        )
        db.add(caregiver)
        db.commit()
        with pytest.raises(ValueError, match="Person not found"):
            process_situation_input(
                db,
                SituationInput(
                    caregiver_id="caregiver-3",
                    person_id="nonexistent",
                    raw_input="Some input",
                ),
            )
        db.close()
    finally:
        teardown_db()


@pytest.mark.session_security
def test_post_situation_empty_input_and_no_documents():
    setup_db()
    try:
        from app.core.database import get_db
        app.dependency_overrides[get_db] = override_get_db

        client = TestClient(app)
        created, headers = create_authenticated_session(client, "Test Person 4")
        response = client.post(
            "/api/situation",
            headers=headers,
            json={
                "caregiver_id": created["caregiver"]["id"],
                "person_id": created["person"]["id"],
                "raw_input": "   ",
                "documents": [],
            },
        )
        assert response.status_code == 400, response.text
        assert "No valid evidence provided" in response.json()["detail"]
    finally:
        teardown_db()



def test_analysis_failure_is_reported_as_partial_after_evidence_is_saved(monkeypatch):
    setup_db()
    try:
        db = TestingSessionLocal()
        person = Person(id="partial-person", name="Partial Person")
        caregiver = Caregiver(
            id="partial-caregiver",
            name="Partial Caregiver",
            email="partial@example.com",
            relationship="Family",
            person_id=person.id,
        )
        db.add_all([person, caregiver])
        db.commit()

        def fail_analysis(*args, **kwargs):
            raise RuntimeError("internal analysis failure")

        monkeypatch.setattr("app.services.situation.detect_event_changes", fail_analysis)
        response = process_situation_input(
            db,
            SituationInput(
                caregiver_id=caregiver.id,
                person_id=person.id,
                raw_input="Breakfast was mostly uneaten.",
            ),
        )

        assert response.ok is True
        assert response.analysis_status == "partial"
        assert response.message == "Care input recorded; analysis is incomplete."
        assert response.analysis_warning
        assert "internal analysis failure" not in response.analysis_warning
        assert db.query(Evidence).filter_by(person_id=person.id).count() == 1
        assert db.query(CareEvent).filter_by(person_id=person.id).count() == 1
        assert response.situations is None
        db.close()
    finally:
        teardown_db()


def test_invalid_observation_timestamp_is_rejected_not_replaced_with_now():
    setup_db()
    try:
        db = TestingSessionLocal()
        person = Person(id="invalid-time-person", name="Invalid Time Person")
        caregiver = Caregiver(
            id="invalid-time-caregiver",
            name="Invalid Time Caregiver",
            email="invalid-time@example.com",
            relationship="Family",
            person_id=person.id,
        )
        db.add_all([person, caregiver])
        db.commit()

        with pytest.raises(ValueError, match="Invalid timestamp"):
            process_situation_input(
                db,
                SituationInput(
                    caregiver_id=caregiver.id,
                    person_id=person.id,
                    raw_input="A reported care observation.",
                    timestamp="not-a-timestamp",
                ),
            )

        assert db.query(Evidence).filter_by(person_id=person.id).count() == 0
        assert db.query(CareEvent).filter_by(person_id=person.id).count() == 0
        db.close()
    finally:
        teardown_db()


@pytest.mark.session_security
def test_lcr_orientation_is_qwen_generated_read_only_and_record_grounded(monkeypatch):
    setup_db()
    try:
        from app.core.database import get_db
        app.dependency_overrides[get_db] = override_get_db
        client = TestClient(app)
        created, headers = create_authenticated_session(client, "LCR Orientation Person")
        person_id = created["person"]["id"]
        caregiver_id = created["caregiver"]["id"]

        saved = client.post(
            "/api/situation",
            headers=headers,
            json={
                "caregiver_id": caregiver_id,
                "person_id": person_id,
                "raw_input": "The person slept poorly last night.",
            },
        )
        assert saved.status_code == 200, saved.text
        before_count = TestingSessionLocal().query(CareEvent).filter_by(person_id=person_id).count()

        captured = {}
        monkeypatch.setattr(main_module, "_ollama_url", lambda: ("http://qwen.test", "qwen-test"))
        def fake_qwen(base, model, messages, options=None):
            captured["base"] = base
            captured["model"] = model
            captured["messages"] = messages
            return "The record notes poor sleep last night. What has changed since then?"
        monkeypatch.setattr(main_module, "_ollama_chat", fake_qwen)

        response = client.post(
            "/api/lcr/orientation",
            headers=headers,
            json={"raw_input": "Hi"},
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["reply"] == "The record notes poor sleep last night. What has changed since then?"
        assert body["ai_generated"] is True
        assert body["care_record_unchanged"] is True
        assert body["companion_history_unchanged"] is True
        assert body["written_to_lcr"] is False
        prompt = captured["messages"][0]["content"]
        assert "slept poorly last night" in prompt
        assert "Do not use canned replies" in prompt
        after_count = TestingSessionLocal().query(CareEvent).filter_by(person_id=person_id).count()
        assert after_count == before_count

        unauthorized = client.post("/api/lcr/orientation", json={"raw_input": "Hi"})
        assert unauthorized.status_code == 401
    finally:
        teardown_db()


def test_lcr_interaction_only_classifier_includes_insults_without_response_templates():
    assert main_module._is_casual_conversation_only("Hi")
    assert main_module._is_casual_conversation_only("This thing is useless.")
    assert main_module._is_casual_conversation_only("You never get anything right")
    assert main_module._is_casual_conversation_only("You are dumb")
    assert not main_module._is_casual_conversation_only("Mom fell last night")


def test_quick_capture_persists_category_and_caregiver_reported_event_time():
    setup_db()
    try:
        from app.core.database import get_db
        app.dependency_overrides[get_db] = override_get_db

        with TestClient(app) as client:
            created, headers = create_authenticated_session(client, "Quick Capture Reported Time")
            event_time = "2026-10-20T08:30:00+00:00"
            original_report = "Caregiver observed restless sleep."
            response = client.post(
                "/api/situation",
                headers=headers,
                json={
                    "caregiver_id": created["caregiver"]["id"],
                    "person_id": created["person"]["id"],
                    "raw_input": original_report,
                    "capture_category": "sleep",
                    "timestamp": event_time,
                    "provenance": {
                        "input_type": "caregiver_observation",
                        "entry_method": "quick_entry",
                        "captured_at": datetime.now(timezone.utc).isoformat(),
                    },
                },
            )
            assert response.status_code == 200, response.text
            data = response.json()
            assert data["events"][0]["time_provenance"] == "caregiver_reported_event_time"
            assert data["events"][0]["occurred_at"].startswith("2026-10-20T08:30:00")
            with TestingSessionLocal() as db:
                event = db.query(CareEvent).filter_by(id=data["care_event_id"]).one()
                evidence = db.query(Evidence).filter_by(id=data["evidence_id"]).one()
                assert "capture_category:sleep" in event.tags
                assert event.time_provenance == "caregiver_reported_event_time"
                assert evidence.source_text == original_report
    finally:
        teardown_db()


def test_quick_capture_without_event_time_marks_entry_time_fallback():
    setup_db()
    db = TestingSessionLocal()
    try:
        person = Person(id="default-time-person", name="Default Time Person")
        caregiver = Caregiver(
            id="default-time-caregiver",
            name="Default Time Caregiver",
            email="default-time@example.com",
            relationship="Family",
            person_id=person.id,
        )
        db.add_all([person, caregiver])
        db.commit()

        response = process_situation_input(
            db,
            SituationInput(
                caregiver_id=caregiver.id,
                person_id=person.id,
                raw_input="Ate breakfast.",
                capture_category="meal",
            ),
        )
        assert response.events[0].time_provenance == "entry_time_defaulted"
        event = db.query(CareEvent).filter_by(id=response.care_event_id).one()
        assert "capture_category:meal" in event.tags
    finally:
        db.close()
        teardown_db()


def test_quick_capture_rejects_invalid_event_timestamp_without_saving():
    setup_db()
    db = TestingSessionLocal()
    try:
        person = Person(id="bad-time-person", name="Bad Time Person")
        caregiver = Caregiver(
            id="bad-time-caregiver",
            name="Bad Time Caregiver",
            email="bad-time@example.com",
            relationship="Family",
            person_id=person.id,
        )
        db.add_all([person, caregiver])
        db.commit()

        with pytest.raises(ValueError, match="Invalid timestamp"):
            process_situation_input(
                db,
                SituationInput(
                    caregiver_id=caregiver.id,
                    person_id=person.id,
                    raw_input="A reported care observation.",
                    capture_category="symptom",
                    timestamp="yesterday morning",
                ),
            )
        assert db.query(Evidence).filter_by(person_id=person.id).count() == 0
        assert db.query(CareEvent).filter_by(person_id=person.id).count() == 0
    finally:
        db.close()
        teardown_db()


def test_quick_capture_rejects_unsupported_category():
    with pytest.raises(ValidationError):
        SituationInput(
            caregiver_id="caregiver",
            person_id="person",
            raw_input="Observed a change.",
            capture_category="unsupported-category",
        )


def test_document_evidence_preserves_extraction_provenance_without_inventing_confidence():
    setup_db()
    db = TestingSessionLocal()
    try:
        person = Person(id="document-provenance-person", name="Document Provenance Person")
        caregiver = Caregiver(
            id="document-provenance-caregiver",
            name="Document Provenance Caregiver",
            email="document-provenance@example.com",
            relationship="Family",
            person_id=person.id,
        )
        db.add_all([person, caregiver])
        db.commit()

        response = process_situation_input(
            db,
            SituationInput(
                caregiver_id=caregiver.id,
                person_id=person.id,
                documents=[
                    {
                        "id": "scan-1",
                        "name": "discharge-scan.png",
                        "extracted_text": "Follow up with cardiology in two weeks.",
                        "mime_type": "image/png",
                        "extraction_source": "ocr",
                        "ocr_confidence": 0.87,
                    }
                ],
                provenance={
                    "input_type": "document",
                    "entry_method": "upload",
                    "captured_at": datetime.now(timezone.utc).isoformat(),
                },
            ),
        )

        evidence = db.query(Evidence).filter_by(id=response.evidence_id).one()
        metadata = __import__("json").loads(evidence.extra_metadata)
        assert evidence.source_type == "document_ocr"
        assert evidence.confidence == 0.87
        assert metadata["extraction_source"] == "ocr"
        assert metadata["mime_type"] == "image/png"
        assert metadata["document_time_status"] == "not_extracted"
        assert metadata["document_id"] == "scan-1"
    finally:
        db.close()
        teardown_db()


def test_document_without_ocr_score_keeps_confidence_unknown():
    setup_db()
    db = TestingSessionLocal()
    try:
        person = Person(id="document-unknown-confidence-person", name="Document Unknown Confidence")
        caregiver = Caregiver(
            id="document-unknown-confidence-caregiver",
            name="Document Unknown Confidence Caregiver",
            email="document-unknown-confidence@example.com",
            relationship="Family",
            person_id=person.id,
        )
        db.add_all([person, caregiver])
        db.commit()

        response = process_situation_input(
            db,
            SituationInput(
                caregiver_id=caregiver.id,
                person_id=person.id,
                documents=[
                    {
                        "id": "pdf-1",
                        "name": "discharge.pdf",
                        "extracted_text": "Follow up in two weeks.",
                        "mime_type": "application/pdf",
                        "extraction_source": "tika",
                    }
                ],
            ),
        )
        evidence = db.query(Evidence).filter_by(id=response.evidence_id).one()
        metadata = __import__("json").loads(evidence.extra_metadata)
        assert evidence.source_type == "document_text_extraction"
        assert evidence.confidence is None
        assert metadata["ocr_confidence"] is None
        assert metadata["document_time_status"] == "not_extracted"
    finally:
        db.close()
        teardown_db()

def test_situation_alternative_confidence_is_not_invented_from_pattern_strength():
    from types import SimpleNamespace
    from app.services.situation_formation import _derive_alternatives

    pattern = SimpleNamespace(
        pattern_type=SimpleNamespace(value="recurrence"),
        subject_type="sleep",
        pattern_strength=SimpleNamespace(value="moderate"),
        evidence_count=3,
    )
    db = TestingSessionLocal()
    try:
        alternatives = _derive_alternatives(db, "person-with-no-change-records", [], [pattern])
    finally:
        db.close()

    assert len(alternatives) == 1
    assert alternatives[0]["confidence"] is None
    assert alternatives[0]["confidence_status"] == "not_calibrated"
    assert "may reflect recording cadence" in alternatives[0]["interpretation"]

