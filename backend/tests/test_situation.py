from datetime import datetime, timezone

import pytest
from pydantic import ValidationError
from fastapi.testclient import TestClient
from sqlalchemy import Column, DateTime, ForeignKey, String, Table, Text, create_engine, func
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.main as main_module
from app.core.database import Base, SessionLocal as DefaultSessionLocal
from app.main import app
from app.models.care import Person, Caregiver, Evidence, CareEvent
from app.models.caregiver_access import CaregiverAccessGrant
from app.models.care_continuity import EvidenceObservation, CareAssertion
from app.models.change import Situation, Change, Baseline
from app.models.uncertainty import OpenQuestion, Contradiction, InformationGap
from app.core.change_enums import SituationType, SituationState, ChangeType, ChangeDirection, SignificanceVerdict
from app.core.uncertainty_enums import Priority
from app.schemas.situation import SituationInput
from app.services.situation import process_situation_input

SQLALCHEMY_DATABASE_URL = "sqlite:///:memory:"

engine = create_engine(
    SQLALCHEMY_DATABASE_URL,
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
if "care_facts" not in Base.metadata.tables:
    Table(
        "care_facts", Base.metadata,
        Column("id", String(36), primary_key=True),
        Column("person_id", String(36), ForeignKey("persons.id"), nullable=False),
        Column("subject_type", String(80), nullable=False),
        Column("subject_id", String(36)),
        Column("fact_type", String(100), nullable=False),
        Column("fact_value", Text, nullable=False),
        Column("epistemic_status", String(32), nullable=False, server_default="reported"),
        Column("effective_at", DateTime(timezone=True)),
        Column("recorded_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
        Column("superseded_by_fact_id", String(36), ForeignKey("care_facts.id")),
        Column("created_by_caregiver_id", String(36), ForeignKey("caregivers.id")),
        Column("review_notes", Text),
        Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
        Column("updated_at", DateTime(timezone=True)),
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
def test_get_situation_rejects_missing_or_invalid_bearer_and_accepts_valid_session():
    """Unauthenticated hydration is rejected; a server-issued session can hydrate its own record."""
    setup_db()
    try:
        from app.core.database import get_db
        app.dependency_overrides[get_db] = override_get_db
        client = TestClient(app)

        missing = client.get("/api/situation")
        assert missing.status_code == 401, missing.text

        invalid = client.get(
            "/api/situation",
            headers={"Authorization": "Bearer not-a-real-session-token"},
        )
        assert invalid.status_code == 401, invalid.text

        created, headers = create_authenticated_session(client, "Authenticated Hydration")
        accepted = client.get(
            f"/api/situation?care_session_id=sess-test-{created['person']['id']}",
            headers=headers,
        )
        assert accepted.status_code == 200, accepted.text
        payload = accepted.json()
        assert payload["care_recipient_id"] == created["person"]["id"]
    finally:
        teardown_db()


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
def test_post_situation_idempotency_replays_same_capture_without_duplicate_evidence_or_event():
    setup_db()
    try:
        from app.core.database import get_db
        app.dependency_overrides[get_db] = override_get_db
        client = TestClient(app)
        created, headers = create_authenticated_session(client, "Idempotent Capture Person")
        person_id = created["person"]["id"]
        caregiver_id = created["caregiver"]["id"]
        payload = {
            "caregiver_id": caregiver_id,
            "person_id": person_id,
            "raw_input": "Breakfast was mostly uneaten; the caregiver offered another meal later.",
            "capture_category": "meal",
            "ingestion_key": "meal-capture-retry-001",
        }

        first = client.post("/api/situation", headers=headers, json=payload)
        assert first.status_code == 200, first.text
        first_data = first.json()

        replay = client.post("/api/situation", headers=headers, json=payload)
        assert replay.status_code == 200, replay.text
        replay_data = replay.json()
        assert replay_data["evidence_id"] == first_data["evidence_id"]
        assert replay_data["care_event_id"] == first_data["care_event_id"]
        assert replay_data["message"] == "This care update was already saved. No duplicate was created."
        assert replay_data["analysis_status"] == "partial"

        with TestingSessionLocal() as db:
            evidence_rows = db.query(Evidence).filter(
                Evidence.person_id == person_id,
                Evidence.ingestion_key == payload["ingestion_key"],
            ).all()
            event_rows = [
                event for event in db.query(CareEvent).filter(
                    CareEvent.person_id == person_id
                ).all()
                if first_data["evidence_id"] in (event.evidence_ids or [])
            ]
            assert len(evidence_rows) == 1
            assert len(event_rows) == 1
            assert evidence_rows[0].ingestion_request_sha256

        changed = {**payload, "raw_input": "A different capture must not reuse the same retry key."}
        conflict = client.post("/api/situation", headers=headers, json=changed)
        assert conflict.status_code == 400, conflict.text
        with TestingSessionLocal() as db:
            assert db.query(Evidence).filter(
                Evidence.person_id == person_id,
                Evidence.ingestion_key == payload["ingestion_key"],
            ).count() == 1
    finally:
        teardown_db()


@pytest.mark.session_security
def test_saved_snap_care_photo_is_linked_to_situation_without_document_ocr():
    setup_db()
    try:
        from app.core.database import get_db
        from app.models.collaboration import EvidenceUpload
        import hashlib
        app.dependency_overrides[get_db] = override_get_db
        client = TestClient(app)
        created, headers = create_authenticated_session(client, "Snap Photo Person")
        person_id = created["person"]["id"]
        caregiver_id = created["caregiver"]["id"]
        from io import BytesIO
        from PIL import Image
        image_buffer = BytesIO()
        Image.new("RGB", (2, 2), (80, 120, 90)).save(image_buffer, format="PNG")
        photo_bytes = image_buffer.getvalue()
        uploaded = client.post(
            "/api/v1/upload",
            headers=headers,
            data={
                "person_id": person_id,
                "caregiver_id": caregiver_id,
                "evidence_type": "care_photo",
            },
            files={"file": ("care-photo.png", photo_bytes, "image/png")},
        )
        assert uploaded.status_code == 201, uploaded.text
        evidence_id = uploaded.json()["evidence_id"]
        response = client.post(
            "/api/situation",
            headers=headers,
            json={
                "caregiver_id": caregiver_id,
                "person_id": person_id,
                "raw_input": "",
                "documents": [{
                    "id": "snap-photo",
                    "name": "care-photo.png",
                    "mime_type": "image/png",
                    "extracted_text": "",
                    "evidence_id": evidence_id,
                    "extraction_source": "unknown",
                }],
            },
        )
        assert response.status_code == 200, response.text
        event_id = response.json()["care_event_id"]
        with TestingSessionLocal() as db:
            evidence = db.query(Evidence).filter_by(id=evidence_id, person_id=person_id).one()
            original = db.query(EvidenceUpload).filter_by(evidence_id=evidence_id).one()
            event = db.query(CareEvent).filter_by(id=event_id, person_id=person_id).one()
            assert evidence.type == "care_photo"
            assert original.content == photo_bytes
            assert original.sha256 == hashlib.sha256(photo_bytes).hexdigest()
            assert evidence_id in event.evidence_ids
            assert db.query(Evidence).filter_by(person_id=person_id).count() == 1
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

        from app.services.situation import _explicit_speaker_statements
        single_speaker = _explicit_speaker_statements("[Nurse]: The person needed support when standing.")
        assert len(single_speaker) == 1
        assert single_speaker[0]["speaker_label"] == "Nurse"
        assert single_speaker[0]["source_passage"] == "The person needed support when standing."
        assert _explicit_speaker_statements("The person needed support when standing.") == []

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
                CareAssertion(
                    id="mobility-claim-for-question",
                    person_id=person_id,
                    subject_type="mobility",
                    subject_id=person_id,
                    field="walking_stability",
                    asserted_value="Unsteady near the kitchen.",
                    source_label="Daughter",
                    evidence_ids=[first_evidence_id],
                    assertion_status="reported",
                ),
                OpenQuestion(
                    id="open-mobility-question",
                    person_id=person_id,
                    question="Has the unsteadiness also happened indoors?",
                    subject_type="mobility",
                    subject_id=person_id,
                    claim_id="mobility-claim-for-question",
                    evidence_ids=[first_evidence_id],
                    status="open",
                    necessity="necessary",
                    intended_answerer_role="reporting_caregiver",
                    necessity_rationale="A current mobility decision depends on clarifying whether the unsteadiness also happens indoors.",
                    priority=Priority.CRITICAL,
                    context=[{
                        "source_kind": "caregiver_report",
                        "source_id": first_evidence_id,
                        "caregiver_attention_required": True,
                        "necessity_assessment": {
                            "necessity_rationale": "A current mobility decision depends on clarifying whether the unsteadiness also happens indoors.",
                            "decision_dependency": "The current mobility handoff cannot accurately describe the observed mobility concern without this clarification.",
                            "evidence_ids": [first_evidence_id],
                        },
                    }],
                ),
                OpenQuestion(
                    id="mobility-question-high",
                    person_id=person_id,
                    question="Has the person needed more support when standing?",
                    subject_type="mobility",
                    subject_id=person_id,
                    evidence_ids=[first_evidence_id],
                    status="open",
                    necessity="necessary",
                    intended_answerer_role="reporting_caregiver",
                    necessity_rationale="The next mobility handoff depends on confirming the support needed when standing.",
                    priority=Priority.HIGH,
                    context=[{
                        "source_kind": "caregiver_report",
                        "source_id": first_evidence_id,
                        "necessity_assessment": {
                            "necessity_rationale": "The next mobility handoff depends on confirming the support needed when standing.",
                            "decision_dependency": "The receiving caregiver cannot describe the current transfer support accurately without this detail.",
                            "evidence_ids": [first_evidence_id],
                        },
                    }],
                ),
                OpenQuestion(
                    id="mobility-question-medium",
                    person_id=person_id,
                    question="Has the person avoided any usual walking route?",
                    subject_type="mobility",
                    subject_id=person_id,
                    evidence_ids=[first_evidence_id],
                    status="open",
                    necessity="necessary",
                    intended_answerer_role="reporting_caregiver",
                    necessity_rationale="The current mobility plan depends on knowing whether a usual route is being avoided.",
                    priority=Priority.MEDIUM,
                    context=[{
                        "source_kind": "caregiver_report",
                        "source_id": first_evidence_id,
                        "necessity_assessment": {
                            "necessity_rationale": "The current mobility plan depends on knowing whether a usual route is being avoided.",
                            "decision_dependency": "The care team cannot review the current walking plan without knowing whether the usual route is still being used.",
                            "evidence_ids": [first_evidence_id],
                        },
                    }],
                ),
                OpenQuestion(
                    id="mobility-question-low",
                    person_id=person_id,
                    question="Would a different time of day make walking easier?",
                    subject_type="mobility",
                    subject_id=person_id,
                    evidence_ids=[first_evidence_id],
                    status="open",
                    necessity="necessary",
                    intended_answerer_role="reporting_caregiver",
                    necessity_rationale="A future mobility review depends on knowing whether timing affects the walking routine.",
                    priority=Priority.LOW,
                    context=[{
                        "source_kind": "caregiver_report",
                        "source_id": first_evidence_id,
                        "necessity_assessment": {
                            "necessity_rationale": "A future mobility review depends on knowing whether timing affects the walking routine.",
                            "decision_dependency": "The next routine review can compare walking at different times if the caregiver records this detail.",
                            "evidence_ids": [first_evidence_id],
                        },
                    }],
                ),
                OpenQuestion(
                    id="unlinked-mobility-question",
                    person_id=person_id,
                    question="Does the unrelated second mobility concern still need review?",
                    subject_type="mobility",
                    subject_id="another-mobility-thread",
                    evidence_ids=[],
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
                InformationGap(
                    id="unassessed-mobility-information-gap",
                    person_id=person_id,
                    subject_type="mobility",
                    subject_id=person_id,
                    field="usual_walking_distance",
                    description="The usual walking distance is not recorded.",
                    evidence_ids=[first_evidence_id],
                    context=[],
                ),
                InformationGap(
                    id="actionable-mobility-information-gap",
                    person_id=person_id,
                    subject_type="mobility",
                    subject_id=person_id,
                    field="current_transfer_support",
                    description="The current transfer support needs confirmation.",
                    evidence_ids=[first_evidence_id],
                    context=[{
                        "caregiver_attention_required": True,
                        "necessity_rationale": "The next mobility handoff depends on this detail.",
                        "decision_dependency": "The receiving caregiver cannot safely follow the handoff without the current transfer support.",
                    }],
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
        assert related_link.json()["relationship_version"] == 1
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
        assert retract_link.json()["relationship_version"] == 2, retract_link.json()
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
        mobility_questions = threads["persisted-mobility-thread"]["openQuestions"]
        # Critical questions remain visible; only the two highest-priority
        # ordinary questions are surfaced initially. The low-priority and
        # unrelated unassessed questions remain internal.
        assert mobility_questions == [
            "Your observation: Has the unsteadiness also happened indoors?",
            "Your observation: Has the person needed more support when standing?",
            "Your observation: Has the person avoided any usual walking route?",
        ]
        assert "Would a different time of day make walking easier?" not in body["return_continuity"]["important_unresolved"]
        assert threads["persisted-mobility-thread"]["conflicts"][0]["winner_selected"] is False
        assert threads["persisted-mobility-thread"]["conflicts"][0]["resolution_status"] == "unresolved"
        assert threads["persisted-appetite-thread"]["openQuestions"] == []
        assert "Does the unrelated second mobility concern still need review?" not in threads["persisted-mobility-thread"]["openQuestions"]
        assert "Does the unrelated second mobility concern still need review?" not in body["return_continuity"]["important_unresolved"]
        assert any(
            item.startswith("Conflicting reports: Daughter reports unsteadiness")
            for item in body["return_continuity"]["important_unresolved"]
        )
        assert threads["persisted-mobility-thread"]["relatedSituations"][0]["id"] == "persisted-appetite-thread"
        assert threads["persisted-appetite-thread"]["relatedSituations"][0]["id"] == "persisted-mobility-thread"
        assert set(threads["persisted-mobility-thread"]["linked_evidence_ids"]).isdisjoint(
            threads["persisted-appetite-thread"]["linked_evidence_ids"]
        )
        assert any("Has the unsteadiness also happened indoors?" in item for item in body["return_continuity"]["important_unresolved"])
        assert not any(
            item.startswith("Information needed: usual_walking_distance")
            for item in body["return_continuity"]["important_unresolved"]
        )
        assert any(
            item.startswith("Information needed: current_transfer_support")
            for item in body["return_continuity"]["important_unresolved"]
        )
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
            return "Welcome back to SolenOS. Your care record notes poor sleep last night; what has changed today?"
        monkeypatch.setattr(main_module, "_ollama_chat", fake_qwen)

        response = client.post(
            "/api/lcr/orientation",
            headers=headers,
            json={"raw_input": "Hi"},
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["reply"] == "Welcome back to SolenOS. Your care record notes poor sleep last night; what has changed today?"
        assert body["ai_generated"] is True
        assert body["care_record_unchanged"] is True
        assert body["companion_history_unchanged"] is True
        assert body["written_to_lcr"] is False
        prompt = captured["messages"][0]["content"]
        assert "slept poorly last night" in prompt
        assert "Respond to the latest message, not to an imagined onboarding flow" in prompt
        assert "Caregiver orientation stage (derived from returned care-domain rows): returning." in prompt
        assert "GREETING LENGTH" in prompt
        assert "RETURNING CARE RECORD" in prompt
        after_count = TestingSessionLocal().query(CareEvent).filter_by(person_id=person_id).count()
        assert after_count == before_count

        unauthorized = client.post("/api/lcr/orientation", json={"raw_input": "Hi"})
        assert unauthorized.status_code == 401
    finally:
        teardown_db()


@pytest.mark.session_security
def test_lcr_orientation_rejects_revoked_caregiver_access(monkeypatch):
    setup_db()
    try:
        from app.core.database import get_db
        app.dependency_overrides[get_db] = override_get_db
        client = TestClient(app)
        created, headers = create_authenticated_session(client, "Revoked Orientation Person")
        db = TestingSessionLocal()
        grant = db.query(CaregiverAccessGrant).filter_by(
            person_id=created["person"]["id"],
            caregiver_id=created["caregiver"]["id"],
        ).one()
        grant.status = "revoked"
        grant.revoked_at = datetime.now(timezone.utc)
        db.commit()
        db.close()

        response = client.post(
            "/api/lcr/orientation",
            headers=headers,
            json={"raw_input": "Hi"},
        )
        assert response.status_code == 403, response.text
    finally:
        teardown_db()


@pytest.mark.session_security
def test_lcr_orientation_recognizes_existing_open_question_as_returning_history(monkeypatch):
    setup_db()
    try:
        from app.core.database import get_db
        app.dependency_overrides[get_db] = override_get_db
        client = TestClient(app)
        created, headers = create_authenticated_session(client, "Open Question Returning Person")
        person_id = created["person"]["id"]
        with TestingSessionLocal() as db:
            db.add(OpenQuestion(
                id="orientation-existing-open-question",
                person_id=person_id,
                question="Has the nighttime waking continued?",
                subject_type="sleep",
                subject_id=person_id,
                status="open",
                necessity="necessary",
                priority=Priority.HIGH,
            ))
            db.commit()

        captured = {}
        monkeypatch.setattr(main_module, "_ollama_url", lambda: ("http://qwen.test", "qwen-test"))

        def fake_qwen(base, model, messages, options=None):
            captured["prompt"] = messages[0]["content"]
            return "Welcome back to SolenOS. Your care record has an open sleep question; we can continue with today’s update."

        monkeypatch.setattr(main_module, "_ollama_chat", fake_qwen)
        response = client.post("/api/lcr/orientation", headers=headers, json={"raw_input": "Hi"})
        assert response.status_code == 200, response.text
        assert "Caregiver orientation stage (derived from returned care-domain rows): returning." in captured["prompt"]
        assert "NEW CARE RECORD" not in captured["prompt"]
        assert response.json()["ai_generated"] is True
        assert response.json()["written_to_lcr"] is False
    finally:
        teardown_db()


@pytest.mark.session_security
def test_lcr_orientation_identifies_new_caregiver_without_fabricating_continuity(monkeypatch):
    setup_db()
    try:
        from app.core.database import get_db
        app.dependency_overrides[get_db] = override_get_db
        client = TestClient(app)
        _, headers = create_authenticated_session(client, "New Orientation Person")

        captured = {}
        monkeypatch.setattr(main_module, "_ollama_url", lambda: ("http://qwen.test", "qwen-test"))

        def fake_qwen(base, model, messages, options=None):
            captured["messages"] = messages
            return "Welcome. SolenOS helps keep your loved one's care story together; start with whatever is happening today."

        monkeypatch.setattr(main_module, "_ollama_chat", fake_qwen)
        response = client.post("/api/lcr/orientation", headers=headers, json={"raw_input": "Hello"})
        assert response.status_code == 200, response.text
        prompt = captured["messages"][0]["content"]
        assert "Caregiver orientation stage (derived from returned care-domain rows): new." in prompt
        assert "NEW CARE RECORD" in prompt
        assert "rough fragments are acceptable" in prompt
        assert response.json()["written_to_lcr"] is False
    finally:
        teardown_db()


@pytest.mark.session_security
def test_lcr_orientation_rejects_returning_language_for_new_caregiver(monkeypatch):
    setup_db()
    try:
        from app.core.database import get_db
        app.dependency_overrides[get_db] = override_get_db
        client = TestClient(app)
        _, headers = create_authenticated_session(client, "New Caregiver Misposition Person")
        monkeypatch.setattr(main_module, "_ollama_url", lambda: ("http://qwen.test", "qwen-test"))
        calls = []

        def misleading_qwen(base, model, messages, options=None):
            calls.append(messages)
            return "Welcome back to SolenOS. Your care record is ready to continue."

        monkeypatch.setattr(main_module, "_ollama_chat", misleading_qwen)
        response = client.post("/api/lcr/orientation", headers=headers, json={"raw_input": "Hi"})
        assert response.status_code == 200, response.text
        body = response.json()
        assert len(calls) == 2
        assert body["ai_generated"] is False
        assert body["provider_status"] == "available_fallback"
        assert "Welcome back" not in body["reply"]
        assert body["written_to_lcr"] is False
    finally:
        teardown_db()





@pytest.mark.parametrize("missing_source", ["events", "evidence", "facts", "assertions"])
@pytest.mark.parametrize("invalid_reply", [
    "Welcome to SolenOS. This is your first time here, and your care story starts today.",
    "Welcome back to SolenOS. Your care story is already underway, and we can continue today.",
])
@pytest.mark.session_security
def test_lcr_orientation_does_not_invent_status_for_incomplete_history(monkeypatch, missing_source, invalid_reply):
    setup_db()
    try:
        from app.core.database import get_db
        app.dependency_overrides[get_db] = override_get_db
        client = TestClient(app)
        _, headers = create_authenticated_session(client, "Incomplete History Person")
        monkeypatch.setattr(
            main_module,
            "build_lcr_context",
            lambda *args, **kwargs: {
                "sources": {"documents": {"returned_count": 1}},
                "missing_required_sources": [missing_source],
            },
        )
        monkeypatch.setattr(
            main_module,
            "format_lcr_context_for_prompt",
            lambda context: "Some required care history sources are unavailable.",
        )
        captured = {}
        calls = []
        outputs = iter([
            invalid_reply,
            "Welcome to SolenOS. We can start with the care story you want to keep track of today.",
        ])
        monkeypatch.setattr(main_module, "_ollama_url", lambda: ("http://qwen.test", "qwen-test"))

        def fake_qwen(base, model, messages, options=None):
            calls.append(messages)
            captured["prompt"] = messages[0]["content"]
            return next(outputs)

        monkeypatch.setattr(main_module, "_ollama_chat", fake_qwen)
        response = client.post("/api/lcr/orientation", headers=headers, json={"raw_input": "Hi"})
        assert response.status_code == 200, response.text
        assert "Caregiver orientation stage (derived from returned care-domain rows): unknown." in captured["prompt"]
        assert "CARE HISTORY STATUS UNKNOWN" in captured["prompt"]
        assert "NEW CARE RECORD" not in captured["prompt"]
        assert len(calls) == 2
        assert "Do not claim the caregiver is new or returning" in calls[1][-1]["content"]
        final_reply = response.json()["reply"].casefold()
        assert not any(
            marker in final_reply
            for marker in ("first time here", "new caregiver", "starting from scratch", "welcome back", "already underway")
        ), final_reply
        assert response.json()["ai_generated"] is True
    finally:
        teardown_db()


def test_lcr_orientation_retries_once_when_qwen_exceeds_greeting_limits(monkeypatch):
    setup_db()
    try:
        from app.core.database import get_db
        app.dependency_overrides[get_db] = override_get_db
        client = TestClient(app)
        _, headers = create_authenticated_session(client, "Greeting Retry Person")
        calls = []

        monkeypatch.setattr(main_module, "_ollama_url", lambda: ("http://qwen.test", "qwen-test"))

        def fake_qwen(base, model, messages, options=None):
            calls.append(messages)
            if len(calls) == 1:
                return " ".join(["This is an unnecessarily long greeting"] * 12)
            return "Welcome to SolenOS. Your care story can start with whatever is happening today."

        monkeypatch.setattr(main_module, "_ollama_chat", fake_qwen)
        response = client.post("/api/lcr/orientation", headers=headers, json={"raw_input": "Hi"})
        assert response.status_code == 200, response.text
        assert len(calls) == 2
        assert "Rewrite your previous answer" in calls[1][-1]["content"]
        assert response.json()["ai_generated"] is True
        assert len(response.json()["reply"].split()) <= 40
    finally:
        teardown_db()


@pytest.mark.session_security
def test_lcr_orientation_retries_generic_greeting_and_rejects_false_continuity(monkeypatch):
    setup_db()
    try:
        from app.core.database import get_db
        app.dependency_overrides[get_db] = override_get_db
        client = TestClient(app)
        _, headers = create_authenticated_session(client, "Greeting Quality Contract")
        outputs = iter([
            "Hi! How can I help?",
            "Welcome to SolenOS. Your care story can start with whatever is happening today.",
        ])
        calls = []

        monkeypatch.setattr(main_module, "_ollama_url", lambda: ("http://qwen.test", "qwen-test"))

        def fake_qwen(base, model, messages, options=None):
            calls.append(messages)
            return next(outputs)

        monkeypatch.setattr(main_module, "_ollama_chat", fake_qwen)
        response = client.post("/api/lcr/orientation", headers=headers, json={"raw_input": "Hi"})
        assert response.status_code == 200, response.text
        body = response.json()
        assert len(calls) == 2
        assert body["ai_generated"] is True
        assert "care story" in body["reply"].casefold()
        assert "how can i help" not in body["reply"].casefold()
        assert "Rewrite your previous answer" in calls[1][-1]["content"]
    finally:
        teardown_db()


@pytest.mark.session_security
def test_lcr_orientation_retries_false_returning_claim_for_new_caregiver(monkeypatch):
    setup_db()
    try:
        from app.core.database import get_db
        app.dependency_overrides[get_db] = override_get_db
        client = TestClient(app)
        _, headers = create_authenticated_session(client, "New Caregiver False Continuity")
        outputs = iter([
            "Welcome back to SolenOS. Your care record is ready.",
            "Welcome to SolenOS. Your care story can start with whatever is happening today.",
        ])
        calls = []

        monkeypatch.setattr(main_module, "_ollama_url", lambda: ("http://qwen.test", "qwen-test"))

        def fake_qwen(base, model, messages, options=None):
            calls.append(messages)
            return next(outputs)

        monkeypatch.setattr(main_module, "_ollama_chat", fake_qwen)
        response = client.post("/api/lcr/orientation", headers=headers, json={"raw_input": "Hello"})
        assert response.status_code == 200, response.text
        assert len(calls) == 2
        assert response.json()["ai_generated"] is True
        assert "welcome back" not in response.json()["reply"].casefold()
    finally:
        teardown_db()


@pytest.mark.session_security
def test_lcr_orientation_uses_explicit_non_ai_greeting_fallback_after_two_invalid_outputs(monkeypatch):
    setup_db()
    try:
        from app.core.database import get_db
        app.dependency_overrides[get_db] = override_get_db
        client = TestClient(app)
        _, headers = create_authenticated_session(client, "Greeting Fallback Person")
        calls = []

        monkeypatch.setattr(main_module, "_ollama_url", lambda: ("http://qwen.test", "qwen-test"))

        def fake_qwen(base, model, messages, options=None):
            calls.append(messages)
            return " ".join(["This answer is too long"] * 20)

        monkeypatch.setattr(main_module, "_ollama_chat", fake_qwen)
        response = client.post("/api/lcr/orientation", headers=headers, json={"raw_input": "Hello"})
        assert response.status_code == 200, response.text
        body = response.json()
        assert len(calls) == 2
        assert body["ai_generated"] is False
        assert body["provider_status"] == "available_fallback"
        assert "keep your loved one's care story together" in body["reply"]
        assert body["written_to_lcr"] is False
    finally:
        teardown_db()


@pytest.mark.session_security
@pytest.mark.parametrize("greeting", ["Good morning, SolenOS!", "Hey there!", "Hello, SolenOS."])
def test_lcr_orientation_recognizes_punctuated_greeting_variants(monkeypatch, greeting):
    setup_db()
    try:
        from app.core.database import get_db
        app.dependency_overrides[get_db] = override_get_db
        client = TestClient(app)
        _, headers = create_authenticated_session(client, "Greeting Variant Person")
        monkeypatch.setattr(main_module, "_ollama_url", lambda: ("http://qwen.test", "qwen-test"))
        captured = {}

        def fake_qwen(base, model, messages, options=None):
            captured["prompt"] = messages[0]["content"]
            return "Welcome to SolenOS. Your care story can start with whatever is happening today."

        monkeypatch.setattr(main_module, "_ollama_chat", fake_qwen)
        response = client.post("/api/lcr/orientation", headers=headers, json={"raw_input": greeting})
        assert response.status_code == 200, response.text
        assert "GREETING LENGTH" in captured["prompt"]
        assert len(response.json()["reply"].split()) <= 40
    finally:
        teardown_db()


@pytest.mark.session_security
def test_lcr_orientation_returns_truthful_greeting_fallback_when_qwen_is_unavailable(monkeypatch):
    setup_db()
    try:
        from app.core.database import get_db
        app.dependency_overrides[get_db] = override_get_db
        client = TestClient(app)
        _, headers = create_authenticated_session(client, "Unavailable Qwen Greeting")
        monkeypatch.setattr(main_module, "_ollama_url", lambda: ("http://qwen.test", "qwen-test"))

        def unavailable(*args, **kwargs):
            raise RuntimeError("Qwen unavailable")

        monkeypatch.setattr(main_module, "_ollama_chat", unavailable)
        response = client.post("/api/lcr/orientation", headers=headers, json={"raw_input": "Hi"})
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["ai_generated"] is False
        assert body["provider_status"] == "unavailable_fallback"
        assert "care story together" in body["reply"]
        assert body["written_to_lcr"] is False
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

@pytest.mark.session_security
def test_lcr_orientation_rejects_client_selected_person_outside_bearer_scope(monkeypatch):
    setup_db()
    try:
        from app.core.database import get_db
        app.dependency_overrides[get_db] = override_get_db
        client = TestClient(app)
        first_session, first_headers = create_authenticated_session(client, "First Orientation Person")
        second_session, _ = create_authenticated_session(client, "Second Orientation Person")
        inference_calls = []

        monkeypatch.setattr(main_module, "_ollama_url", lambda: ("http://qwen.test", "qwen-test"))

        def fake_qwen(base, model, messages, options=None):
            inference_calls.append(messages)
            return "Welcome to SolenOS. What would you like to record today?"

        monkeypatch.setattr(main_module, "_ollama_chat", fake_qwen)
        response = client.post(
            "/api/lcr/orientation",
            headers=first_headers,
            json={
                "raw_input": "Hi",
                "person_id": second_session["person"]["id"],
            },
        )
        assert response.status_code == 403, response.text
        assert inference_calls == []
        assert first_session["person"]["id"] != second_session["person"]["id"]
    finally:
        teardown_db()

@pytest.mark.session_security
def test_situation_freshness_includes_persisted_delta_engine_changes():
    setup_db()
    try:
        from app.core.database import get_db
        app.dependency_overrides[get_db] = override_get_db
        client = TestClient(app)
        created, headers = create_authenticated_session(client, "Delta Freshness Person")
        person_id = created["person"]["id"]

        initial = client.post(
            "/api/v1/care-continuity/situation-understanding/reassess",
            headers=headers,
        )
        assert initial.status_code == 200, initial.text
        assert initial.json()["status"] == "created"

        current_before = client.get(
            "/api/v1/care-continuity/situation-understanding",
            headers=headers,
        )
        assert current_before.status_code == 200, current_before.text
        assert current_before.json()["source_freshness"]["status"] == "current_for_bounded_source_view"

        with TestingSessionLocal() as db:
            db.add(Change(
                id="freshness-delta-change",
                person_id=person_id,
                subject_type="mobility",
                subject_id=person_id,
                change_type=ChangeType.TRANSITION,
                change_direction=ChangeDirection.UNKNOWN,
                previous_state="Previously recorded state",
                current_state="Newly recorded state",
                evidence_ids=[],
                explanation="A persisted Delta Engine candidate was added after the snapshot.",
                significance_verdict=SignificanceVerdict.MEANINGFUL_CANDIDATE,
                attention_candidate=True,
                follow_up_candidate=False,
                confirmation_status="detected",
                is_active=True,
                baseline_coverage_status="covered",
                current_coverage_status="covered",
            ))
            db.commit()

        current_after = client.get(
            "/api/v1/care-continuity/situation-understanding",
            headers=headers,
        )
        assert current_after.status_code == 200, current_after.text
        assert current_after.json()["source_freshness"]["status"] == "source_changed_since_assessment"
        assert current_after.json()["source_freshness"]["reassessment_recommended"] is True
    finally:
        teardown_db()


@pytest.mark.session_security
def test_situation_freshness_includes_new_persisted_care_events():
    """A newly saved Daily Care event must invalidate the prior situation snapshot."""
    setup_db()
    try:
        from app.core.database import get_db
        app.dependency_overrides[get_db] = override_get_db
        client = TestClient(app)
        created, headers = create_authenticated_session(client, "Care Event Freshness Person")
        person_id = created["person"]["id"]
        caregiver_id = created["caregiver"]["id"]

        initial = client.post(
            "/api/v1/care-continuity/situation-understanding/reassess",
            headers=headers,
        )
        assert initial.status_code == 200, initial.text
        assert initial.json()["status"] == "created"

        current_before = client.get(
            "/api/v1/care-continuity/situation-understanding",
            headers=headers,
        )
        assert current_before.status_code == 200, current_before.text
        assert current_before.json()["source_freshness"]["status"] == "current_for_bounded_source_view"

        with TestingSessionLocal() as db:
            db.add(CareEvent(
                id="freshness-new-daily-care-event",
                person_id=person_id,
                event_type="mobility",
                status="recorded",
                occurred_at=datetime(2026, 11, 14, 9, 0, tzinfo=timezone.utc),
                title="Needed help standing",
                description="Caregiver recorded that standing support was needed.",
                evidence_ids=[],
                created_by_caregiver_id=caregiver_id,
                time_provenance="caregiver_observed",
            ))
            db.commit()

        current_after = client.get(
            "/api/v1/care-continuity/situation-understanding",
            headers=headers,
        )
        assert current_after.status_code == 200, current_after.text
        assert current_after.json()["source_freshness"]["status"] == "source_changed_since_assessment"
        assert current_after.json()["source_freshness"]["reassessment_recommended"] is True

        reassessed = client.post(
            "/api/v1/care-continuity/situation-understanding/reassess",
            headers=headers,
        )
        assert reassessed.status_code == 200, reassessed.text
        assert reassessed.json()["status"] == "created"
        assert reassessed.json()["assessment_id"] != initial.json()["assessment_id"]

        current_after_reassessment = client.get(
            "/api/v1/care-continuity/situation-understanding",
            headers=headers,
        )
        assert current_after_reassessment.status_code == 200, current_after_reassessment.text
        assert current_after_reassessment.json()["assessment_id"] == reassessed.json()["assessment_id"]
        assert current_after_reassessment.json()["source_freshness"]["status"] == "current_for_bounded_source_view"

        with TestingSessionLocal() as db:
            event = db.query(CareEvent).filter_by(
                id="freshness-new-daily-care-event",
                person_id=person_id,
            ).one()
            event.description = "Caregiver corrected the standing-support detail."
            db.commit()

        after_event_correction = client.get(
            "/api/v1/care-continuity/situation-understanding",
            headers=headers,
        )
        assert after_event_correction.status_code == 200, after_event_correction.text
        assert after_event_correction.json()["source_freshness"]["status"] == "source_changed_since_assessment"
        assert after_event_correction.json()["source_freshness"]["reassessment_recommended"] is True
    finally:
        teardown_db()


@pytest.mark.session_security
def test_situation_freshness_detects_changes_outside_bounded_lcr_page():
    """A source change beyond the 50-row LCR page still invalidates the snapshot."""
    from datetime import timedelta

    setup_db()
    try:
        from app.core.database import get_db
        app.dependency_overrides[get_db] = override_get_db
        client = TestClient(app)
        created, headers = create_authenticated_session(client, "Bounded History Freshness Person")
        person_id = created["person"]["id"]
        caregiver_id = created["caregiver"]["id"]
        reference = datetime(2026, 11, 15, 12, 0, tzinfo=timezone.utc)

        with TestingSessionLocal() as db:
            for index in range(51):
                db.add(CareEvent(
                    id=f"bounded-freshness-event-{index:03d}",
                    person_id=person_id,
                    event_type="mobility",
                    status="recorded",
                    occurred_at=reference - timedelta(days=index),
                    title=f"Recorded mobility update {index}",
                    description=f"Caregiver recorded mobility update number {index}.",
                    evidence_ids=[],
                    created_by_caregiver_id=caregiver_id,
                    time_provenance="caregiver_observed",
                ))
            db.commit()

        initial = client.post(
            "/api/v1/care-continuity/situation-understanding/reassess",
            headers=headers,
        )
        assert initial.status_code == 200, initial.text

        with TestingSessionLocal() as db:
            db.add(CareEvent(
                id="bounded-freshness-event-older-than-page",
                person_id=person_id,
                event_type="mobility",
                status="recorded",
                occurred_at=reference - timedelta(days=120),
                title="Older mobility update outside the recent page",
                description="This persisted source is older than the 50-row LCR page.",
                evidence_ids=[],
                created_by_caregiver_id=caregiver_id,
                time_provenance="caregiver_observed",
            ))
            db.commit()

        current = client.get(
            "/api/v1/care-continuity/situation-understanding",
            headers=headers,
        )
        assert current.status_code == 200, current.text
        assert current.json()["source_freshness"]["status"] == "source_changed_since_assessment"
        assert current.json()["source_freshness"]["reassessment_recommended"] is True
    finally:
        teardown_db()

@pytest.mark.session_security
def test_saved_situation_assessment_survives_freshness_rebuild_failure(monkeypatch):
    """A failed freshness check must not hide the last persisted care picture."""
    setup_db()
    try:
        from app.core.database import get_db
        app.dependency_overrides[get_db] = override_get_db
        client = TestClient(app)
        created, headers = create_authenticated_session(client, "Freshness Failure Person")
        person_id = created["person"]["id"]

        saved = client.post(
            "/api/v1/care-continuity/situation-understanding/reassess",
            headers=headers,
        )
        assert saved.status_code == 200, saved.text
        assert saved.json()["assessment"]["person_id"] == person_id

        def fail_freshness_rebuild(*args, **kwargs):
            raise RuntimeError("simulated bounded source refresh failure")

        monkeypatch.setattr(
            "app.api.care_continuity._build_situation_understanding_assessment",
            fail_freshness_rebuild,
        )
        response = client.get(
            "/api/v1/care-continuity/situation-understanding",
            headers=headers,
        )

        assert response.status_code == 200, response.text
        body = response.json()
        assert body["assessment"]["person_id"] == person_id
        assert body["assessment_id"] == saved.json()["assessment_id"]
        assert body["source_freshness"]["status"] == "freshness_check_unavailable"
        assert body["source_freshness"]["reassessment_recommended"] is True
        assert "potentially out of date" in body["source_freshness"]["limitation"]
    finally:
        teardown_db()

@pytest.mark.session_security
def test_baseline_freshness_is_partial_schema_safe_and_quality_gated():
    setup_db()
    try:
        from app.core.database import get_db
        app.dependency_overrides[get_db] = override_get_db
        client = TestClient(app)
        created, headers = create_authenticated_session(client, "Baseline Freshness Person")
        person_id = created["person"]["id"]

        with TestingSessionLocal() as db:
            db.add(Baseline(
                id="freshness-baseline",
                person_id=person_id,
                subject_type="mobility",
                subject_id=person_id,
                window_start=datetime(2026, 10, 1, tzinfo=timezone.utc),
                window_end=datetime(2026, 10, 15, tzinfo=timezone.utc),
                event_count=3,
                state_value="usual",
                coverage_status="covered",
                history_completeness="complete",
                is_stable=True,
                is_active=True,
            ))
            db.add(Change(
                id="freshness-baseline-change",
                person_id=person_id,
                subject_type="mobility",
                subject_id=person_id,
                change_type=ChangeType.TRANSITION,
                change_direction=ChangeDirection.UNKNOWN,
                previous_state="Previously recorded state",
                current_state="Newly recorded state",
                baseline_id="freshness-baseline",
                evidence_ids=[],
                explanation="A persisted Delta Engine candidate references the baseline.",
                significance_verdict=SignificanceVerdict.MEANINGFUL_CANDIDATE,
                attention_candidate=True,
                follow_up_candidate=False,
                confirmation_status="detected",
                is_active=True,
                baseline_coverage_status="covered",
                current_coverage_status="covered",
            ))
            db.commit()
            # Simulate a legacy schema that has the required comparison fields
            # but lacks an optional mapped column. The assessment must select
            # only the columns it actually needs.
            db.connection().exec_driver_sql("ALTER TABLE baselines DROP COLUMN updated_at")
            db.commit()

        initial = client.post(
            "/api/v1/care-continuity/situation-understanding/reassess",
            headers=headers,
        )
        assert initial.status_code == 200, initial.text
        assert initial.json()["assessment"]["care_attention"]["source_coverage"]["persisted_baseline_source_complete"] is True

        current_before = client.get(
            "/api/v1/care-continuity/situation-understanding",
            headers=headers,
        )
        assert current_before.status_code == 200, current_before.text
        assert current_before.json()["source_freshness"]["status"] == "current_for_bounded_source_view"

        with TestingSessionLocal() as db:
            db.connection().exec_driver_sql(
                "UPDATE baselines SET is_stable = 0 WHERE id = ?",
                ("freshness-baseline",),
            )
            db.commit()

        current_after = client.get(
            "/api/v1/care-continuity/situation-understanding",
            headers=headers,
        )
        assert current_after.status_code == 200, current_after.text
        assert current_after.json()["source_freshness"]["status"] == "source_changed_since_assessment"

        with TestingSessionLocal() as db:
            db.connection().exec_driver_sql(
                "UPDATE baselines SET is_active = 0 WHERE id = ?",
                ("freshness-baseline",),
            )
            db.commit()

        incomplete = client.post(
            "/api/v1/care-continuity/situation-understanding/reassess",
            headers=headers,
        )
        assert incomplete.status_code == 200, incomplete.text
        assessment = incomplete.json()["assessment"]
        assert assessment["coverage"]["is_complete"] is False
        assert "persisted_change_baseline_incomplete" in assessment["assessment_quality"]["blocking_limitations"]
        assert assessment["care_attention"]["source_coverage"]["persisted_baseline_source_complete"] is False
    finally:
        teardown_db()

@pytest.mark.session_security
def test_situation_freshness_includes_persisted_contradictions():
    setup_db()
    try:
        from app.core.database import get_db
        app.dependency_overrides[get_db] = override_get_db
        client = TestClient(app)
        created, headers = create_authenticated_session(client, "Contradiction Freshness Person")
        person_id = created["person"]["id"]

        initial = client.post(
            "/api/v1/care-continuity/situation-understanding/reassess",
            headers=headers,
        )
        assert initial.status_code == 200, initial.text

        current_before = client.get(
            "/api/v1/care-continuity/situation-understanding",
            headers=headers,
        )
        assert current_before.status_code == 200, current_before.text
        assert current_before.json()["source_freshness"]["status"] == "current_for_bounded_source_view"

        with TestingSessionLocal() as db:
            db.add(Contradiction(
                id="freshness-contradiction",
                person_id=person_id,
                subject_type="mobility",
                subject_id=person_id,
                field="mobility",
                description="Two saved reports describe different mobility states.",
                matching_candidate_ids=[],
                evidence_ids=[],
                source_assertion_ids=[],
                resolution_status="unresolved",
            ))
            db.commit()

        current_after = client.get(
            "/api/v1/care-continuity/situation-understanding",
            headers=headers,
        )
        assert current_after.status_code == 200, current_after.text
        assert current_after.json()["source_freshness"]["status"] == "source_changed_since_assessment"
        assert current_after.json()["source_freshness"]["reassessment_recommended"] is True
    finally:
        teardown_db()

@pytest.mark.session_security
def test_caregiver_situation_state_changes_require_linked_sources_and_persist_history():
    setup_db()
    try:
        from app.core.database import get_db

        app.dependency_overrides[get_db] = override_get_db
        client = TestClient(app)
        created, headers = create_authenticated_session(client, "Situation state transition person")
        person_id = created["person"]["id"]
        caregiver_id = created["caregiver"]["id"]
        now = datetime.now(timezone.utc)

        with TestingSessionLocal() as db:
            other_person = Person(id="state-transition-other-person", name="Other person")
            other_caregiver = Caregiver(
                id="state-transition-other-caregiver",
                name="Other caregiver",
                relationship="Family",
                person_id=other_person.id,
            )
            evidence = Evidence(
                id="state-transition-evidence",
                person_id=person_id,
                type="care_note",
                source_text="Caregiver reports more help is needed with walking.",
                uploaded_by_caregiver_id=caregiver_id,
                reported_by_label="Family caregiver",
                evidence_status="verified",
                occurred_at=now,
            )
            other_evidence = Evidence(
                id="state-transition-other-evidence",
                person_id=other_person.id,
                type="care_note",
                source_text="A separate person's private mobility note.",
                uploaded_by_caregiver_id=other_caregiver.id,
                reported_by_label="Family caregiver",
                evidence_status="verified",
                occurred_at=now,
            )
            event = CareEvent(
                id="state-transition-event",
                person_id=person_id,
                event_type="mobility",
                status="recorded",
                occurred_at=now,
                title="Walking support observation",
                description="Caregiver reports more help is needed with walking.",
                evidence_ids=[evidence.id],
                created_by_caregiver_id=caregiver_id,
            )
            assertion = CareAssertion(
                id="state-transition-assertion",
                person_id=person_id,
                subject_type="person",
                subject_id=person_id,
                field="mobility",
                assertion_kind="current_state",
                asserted_value="Needs more help when walking",
                source_event_id="state-transition-event",
                source_label="Family caregiver",
                source_text="Caregiver reports more help is needed with walking.",
                # The assertion itself has no direct evidence link; provenance
                # must resolve through source_event_id to the event's evidence.
                evidence_ids=[],
                assertion_status="reported",
                epistemic_state="reported",
                observed_at=now,
                created_by_caregiver_id=caregiver_id,
            )
            situation = Situation(
                id="state-transition-situation",
                person_id=person_id,
                situation_type=SituationType.UNKNOWN,
                state=SituationState.ACTIVE,
                title="Walking support",
                description="A saved mobility situation.",
                signal_ids=[assertion.id],
                signal_types=["mobility"],
                temporal_start=now,
                temporal_end=now,
                change_ids=[],
                pattern_ids=[],
                context={},
                is_active=True,
            )
            db.add_all([other_person, other_caregiver, evidence, other_evidence, event, assertion, situation])
            db.commit()
            situation_id = situation.id

        transition = client.post(
            f"/api/v1/change/situations/{person_id}/{situation_id}/state",
            headers=headers,
            json={
                "state": "worsening",
                "reason": "The caregiver reports more help is needed.",
                "evidence_ids": ["state-transition-evidence"],
            },
        )
        assert transition.status_code == 200, transition.text
        transition_body = transition.json()
        assert transition_body["previous_state"] == "active"
        assert transition_body["new_state"] == "worsening"
        assert transition_body["actor_caregiver_id"] == caregiver_id
        assert transition_body["evidence_ids"] == ["state-transition-evidence"]

        resolved = client.post(
            f"/api/v1/change/situations/{person_id}/{situation_id}/state",
            headers=headers,
            json={
                "state": "resolved",
                "reason": "The caregiver confirms this concern has settled.",
                "evidence_ids": ["state-transition-evidence"],
            },
        )
        assert resolved.status_code == 200, resolved.text
        assert resolved.json()["previous_state"] == "worsening"
        assert resolved.json()["new_state"] == "resolved"

        detail = client.get(
            f"/api/v1/change/situations/{person_id}/{situation_id}/detail",
            headers=headers,
        )
        assert detail.status_code == 200, detail.text
        detail_sources = detail.json()["source_records"]
        detail_source_ids = {item["id"] for item in detail_sources}
        assert {
            "state-transition-assertion",
            "state-transition-event",
            "state-transition-evidence",
        }.issubset(detail_source_ids)
        detail_by_id = {item["id"]: item for item in detail_sources}
        assert detail_by_id["state-transition-event"]["excerpt"] == "Caregiver reports more help is needed with walking."
        assert detail_by_id["state-transition-evidence"]["excerpt"] is None, (
            "the linked evidence row must retain provenance without repeating the same caregiver-authored sentence"
        )
        history = detail.json()["state_history"]
        assert [entry["new_state"] for entry in history] == ["worsening", "resolved"]
        assert all(entry["source"] == "caregiver_confirmed" for entry in history)
        assert all(entry["evidence_ids"] == ["state-transition-evidence"] for entry in history)

        cross_person = client.post(
            f"/api/v1/change/situations/{person_id}/{situation_id}/state",
            headers=headers,
            json={
                "state": "active",
                "reason": "Attempt to attach another person's private note.",
                "evidence_ids": ["state-transition-other-evidence"],
            },
        )
        assert cross_person.status_code == 422
        refreshed = client.get(
            f"/api/v1/change/situations/{person_id}/{situation_id}/detail",
            headers=headers,
        )
        assert refreshed.status_code == 200, refreshed.text
        assert refreshed.json()["situation"]["state"] == "resolved"
        assert len(refreshed.json()["state_history"]) == 2
    finally:
        teardown_db()



def test_care_source_excerpt_extracts_original_daily_capture_without_storage_json():
    from app.api.change import _care_source_excerpt

    source = {
        "original_capture": {
            "observation": "Caregiver reports restless sleep and three overnight awakenings.",
            "source": "critical_care_journey_e2e",
            "capture_idempotency_key": "internal-only-key",
        }
    }
    excerpt = _care_source_excerpt(__import__("json").dumps(source))

    assert excerpt == "Caregiver reports restless sleep and three overnight awakenings."
    assert "original_capture" not in excerpt
    assert "internal-only-key" not in excerpt
