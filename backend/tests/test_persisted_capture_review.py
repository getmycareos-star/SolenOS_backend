import asyncio
import json

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Column, DateTime, ForeignKey, Integer, JSON, String, Table, Text, UniqueConstraint, create_engine, func
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.main as main_module
import app.api.v1 as api_v1_module
from app.core.database import Base, get_db, SessionLocal as DefaultSessionLocal
from app.main import app
from app.models.capture_analysis import CaptureAnalysis
from app.models.caregiver_access import CaregiverAccessGrant
from app.models.care import CareEvent, Evidence
from app.models.care_continuity import CareAssertion, EvidenceObservation
from app.services.capture_review import (
    _source_grounded_weight_loss_detail,
    persist_source_grounded_daily_observations,
)
from app.models.uncertainty import OpenQuestion

pytestmark = pytest.mark.session_security

engine = create_engine(
    "sqlite:///:memory:",
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
if "care_assertion_revisions" not in Base.metadata.tables:
    Table(
        "care_assertion_revisions", Base.metadata,
        Column("id", String(36), primary_key=True),
        Column("person_id", String(36), ForeignKey("persons.id"), nullable=False),
        Column("assertion_id", String(36), ForeignKey("care_assertions.id"), nullable=False),
        Column("revision_number", Integer, nullable=False),
        Column("previous_payload", JSON),
        Column("current_payload", JSON),
        Column("revision_reason", Text),
        Column("provenance_id", String(36)),
        Column("changed_by_caregiver_id", String(36), ForeignKey("caregivers.id")),
        Column("changed_at", DateTime(timezone=True)),
        UniqueConstraint("assertion_id", "revision_number"),
    )

TestSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def override_get_db():
    db = TestSessionLocal()
    try:
        yield db
    finally:
        db.close()


def setup_function():
    Base.metadata.create_all(bind=engine)
    app.dependency_overrides[get_db] = override_get_db
    main_module.SessionLocal = TestSessionLocal


def teardown_function():
    app.dependency_overrides.clear()
    main_module.SessionLocal = DefaultSessionLocal
    Base.metadata.drop_all(bind=engine)


def process_daily_capture_job(job_id, monkeypatch, *, extractor=None):
    from datetime import datetime, timedelta, timezone
    from app.models.qwen_background_job import QwenBackgroundJob
    from app.services import qwen_background_worker

    monkeypatch.setattr(qwen_background_worker, "SessionLocal", TestSessionLocal)
    monkeypatch.setattr(
        qwen_background_worker,
        "extract_review_candidates",
        extractor if extractor is not None else api_v1_module.extract_review_candidates,
    )
    db = TestSessionLocal()
    try:
        job = db.query(QwenBackgroundJob).filter_by(id=job_id).one()
        job.available_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        db.commit()
    finally:
        db.close()
    return asyncio.run(qwen_background_worker.process_one_qwen_job(job_id=job_id))


def create_session(client, name):
    response = client.post(
        "/api/v1/sessions/anonymous",
        json={"name": name, "caregiver_name": "Family caregiver"},
    )
    assert response.status_code == 201, response.text
    return response.json()




def test_weight_loss_normalization_preserves_negation_and_hedging():
    client = TestClient(app)
    session = create_session(client, "Weight normalization recipient")
    db = TestSessionLocal()
    try:
        negated = _source_grounded_weight_loss_detail(
            db,
            person_id=session["person"]["id"],
            evidence_id="not-yet-persisted",
            source_text="She is not losing weight.",
            observation_text="not losing weight",
        )
        assert negated is None

        hedged = _source_grounded_weight_loss_detail(
            db,
            person_id=session["person"]["id"],
            evidence_id="not-yet-persisted",
            source_text="She may be losing weight.",
            observation_text="losing weight",
        )
        assert hedged is not None
        assert hedged["normalized_value"] == "possible weight loss"
        assert hedged["epistemic_status"] == "uncertain"
        assert hedged["hedge_marker"].casefold() == "may be"
    finally:
        db.close()


def test_structured_daily_capture_persists_source_linked_details_without_model_extraction(monkeypatch):
    client = TestClient(app)
    session = create_session(client, "Structured capture recipient")
    auth = {"Authorization": f"Bearer {session['access_token']}"}
    payload = {
        "person_id": session["person"]["id"],
        "caregiver_id": session["caregiver"]["id"],
        "capture_idempotency_key": "structured-capture-key-001",
        "notes": "she's loosing weight",
        "medications": [],
        "scales": [{
            "axis": "mood",
            "note": "she's sad",
            "observed_at": "2026-11-11T21:10:00.000Z",
            "score": 5,
        }],
        "vitals": [{
            "measured_at": "2026-11-11T21:10:00.000Z",
            "note": "yes",
            "unit": "mmHg",
            "value": 10,
            "vital_type": "bp_systolic",
        }],
    }

    saved = client.post("/api/v1/daily/log", headers=auth, json=payload)
    assert saved.status_code == 200, saved.text
    body = saved.json()
    assert body["structured_detail_count"] == 4
    assert body["execution_status"]["structured_details"] == "verified"

    db = TestSessionLocal()
    try:
        evidence = db.query(Evidence).filter_by(
            person_id=session["person"]["id"],
            id=body["evidence_ids"][0],
        ).one()
        assert "capture_idempotency_key" not in evidence.source_text
        assert "capture_request_fingerprint" not in evidence.source_text
        assert "she's loosing weight" in evidence.source_text
        details = db.query(EvidenceObservation).filter_by(
            person_id=session["person"]["id"],
            evidence_id=evidence.id,
            extraction_method="structured_daily_capture",
        ).all()
        assert {item.attribute for item in details} == {
            "caregiver_note", "mood", "blood_pressure_systolic", "reported_weight_change",
        }
        assert all(item.epistemic_status == "reported" for item in details)
        assert all(item.recorded_at is not None for item in details)
        caregiver_note = next(item for item in details if item.attribute == "caregiver_note")
        weight_change = next(item for item in details if item.attribute == "reported_weight_change")
        assert caregiver_note.observed_at is None and caregiver_note.occurred_at is None
        assert weight_change.value_text == "she's loosing weight"
        assert weight_change.normalized_value == "possible weight loss"
        assert weight_change.epistemic_status == "reported"
        assert weight_change.observation_kind == "caregiver_report"
        assert weight_change.observed_at is None and weight_change.occurred_at is None
        assert weight_change.source_passage == json.dumps("she's loosing weight", ensure_ascii=False)
        assert evidence.source_text[weight_change.source_span_start:weight_change.source_span_end] == weight_change.source_passage
        assert "not independently confirmed" in weight_change.context
        assert "No amount was stated" in weight_change.context
        assert "No timeframe was stated" in weight_change.context
        assert "No previous weight measurement was found" in weight_change.context
        assert "Clinical interpretation has not been established" in weight_change.context
        assert "amount" in weight_change.ambiguity_note
        assert "timeframe" in weight_change.ambiguity_note
        assert "previous weight measurement" in weight_change.ambiguity_note
        assert next(item for item in details if item.attribute == "mood").observed_at is not None
        assert next(item for item in details if item.attribute == "blood_pressure_systolic").observed_at is not None
        assert all(evidence.source_text[item.source_span_start:item.source_span_end] == item.source_passage for item in details)
        assert next(item for item in details if item.attribute == "mood").value_text == "5"
        mood = next(item for item in details if item.attribute == "mood")
        blood_pressure = next(item for item in details if item.attribute == "blood_pressure_systolic")
        assert mood.units == "score"
        assert mood.source_passage == json.dumps({
            "axis": "mood", "note": "she's sad",
            "observed_at": "2026-11-11T21:10:00.000Z", "score": 5,
        }, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        assert blood_pressure.value_text == "10"
        assert blood_pressure.units == "mmHg"
        assert blood_pressure.normalized_value is None
        assert "not validated as a clinically usable measurement" in blood_pressure.context
        assert "she's sad" in mood.context
        assert blood_pressure.source_passage == json.dumps({
            "measured_at": "2026-11-11T21:10:00.000Z", "note": "yes",
            "unit": "mmHg", "value": 10, "vital_type": "bp_systolic",
        }, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        assert next(item for item in details if item.attribute == "caregiver_note").source_passage == json.dumps("she's loosing weight", ensure_ascii=False)
        assert next(item for item in details if item.attribute == "blood_pressure_systolic").units == "mmHg"
        # A later source-grounded extraction of the same note must reuse the
        # deterministic normalized detail rather than create a duplicate.
        duplicate_projection = persist_source_grounded_daily_observations(
            db,
            person_id=session["person"]["id"],
            caregiver_id=session["caregiver"]["id"],
            evidence_id=evidence.id,
            source_event_id=body["id"],
            source_text=evidence.source_text,
            candidates={"observations": [{
                "subject": "she",
                "observation": "loosing weight",
                "source_quote": json.dumps("she's loosing weight", ensure_ascii=False),
            }]},
            reported_at=evidence.created_at,
        )
        assert duplicate_projection == 0
        assert db.query(EvidenceObservation).filter_by(
            person_id=session["person"]["id"],
            evidence_id=evidence.id,
            attribute="reported_weight_change",
        ).count() == 1
    finally:
        db.close()

    replay = client.post("/api/v1/daily/log", headers=auth, json=payload)
    assert replay.status_code == 200, replay.text
    assert replay.json()["idempotent_replay"] is True
    assert replay.json()["structured_detail_count"] == 4

    db = TestSessionLocal()
    try:
        assert db.query(EvidenceObservation).filter_by(
            person_id=session["person"]["id"],
            evidence_id=body["evidence_ids"][0],
            extraction_method="structured_daily_capture",
        ).count() == 4
    finally:
        db.close()

def test_daily_capture_correction_then_acceptance_creates_source_linked_lcr_assertion(monkeypatch):
    client = TestClient(app)
    session = create_session(client, "Care recipient")
    auth = {"Authorization": f"Bearer {session['access_token']}"}
    source = "Caregiver report: Mum slept badly last night."
    candidates = {
        "observations": [{
            "subject": "sleep",
            "observation": "Mum slept badly last night.",
            "source_quote": "Mum slept badly last night.",
            "review_status": "pending_review",
            "source_event_id": "event-from-capture",
        }],
        "open_questions": ["How long has this been happening?"],
        "possible_safety_flags": [],
    }
    def fake_extract_review_candidates(source_text, *, source_event_id, source_evidence_id=None):
        return {
            **candidates,
            "observations": [
                {**candidate, "source_event_id": source_event_id}
                for candidate in candidates["observations"]
            ],
        }

    monkeypatch.setattr(api_v1_module, "extract_review_candidates", fake_extract_review_candidates)

    saved = client.post(
        "/api/v1/daily/log",
        headers=auth,
        json={
            "person_id": session["person"]["id"],
            "caregiver_id": session["caregiver"]["id"],
            "notes": source,
            "medications": [],
            "vitals": [],
            "scales": [],
        },
    )
    assert saved.status_code == 200, saved.text
    body = saved.json()
    assert body["qwen_extraction_status"] == "queued"
    assert body["qwen_job_id"]
    assert body["qwen_analysis_id"]
    assert body["qwen_written_to_lcr"] is False
    process_daily_capture_job(body["qwen_job_id"], monkeypatch)

    db = TestSessionLocal()
    try:
        # A generic model-suggested question without a persisted, necessary
        # LCR gap must not become another task for the caregiver.
        assert db.query(OpenQuestion).filter_by(
            person_id=session["person"]["id"],
            question="How long has this been happening?",
            status="open",
            necessity="necessary",
        ).count() == 0
    finally:
        db.close()

    reviewed = client.post(
        f"/api/v1/capture/analyses/{body['qwen_analysis_id']}/review",
        headers=auth,
        json={
            "candidate_index": 0,
            "decision": "correct",
            "corrected_observation": "Mum slept poorly last night.",
        },
    )
    assert reviewed.status_code == 200, reviewed.text
    result = reviewed.json()
    assert result["candidate"]["observation"] == "Mum slept badly last night."
    assert result["candidate"]["corrected_observation"] == "Mum slept poorly last night."
    assert result["candidate"]["review_status"] == "corrected_pending_approval"
    assert result["candidate"]["reviewed_by_caregiver_id"] == session["caregiver"]["id"]
    assert result["written_to_lcr"] is False
    assert "remains pending explicit approval" in result["message"]

    persisted_correction = client.get(
        f"/api/v1/capture/analyses/{body['qwen_analysis_id']}",
        headers=auth,
    )
    assert persisted_correction.status_code == 200, persisted_correction.text
    persisted_candidate = persisted_correction.json()["candidates"]["observations"][0]
    assert persisted_candidate["observation"] == "Mum slept badly last night."
    assert persisted_candidate["corrected_observation"] == "Mum slept poorly last night."
    assert persisted_candidate["review_status"] == "corrected_pending_approval"
    assert persisted_correction.json()["review_required"] is True
    assert persisted_correction.json()["written_to_lcr"] is False

    db = TestSessionLocal()
    try:
        assert db.query(CareAssertion).filter_by(person_id=session["person"]["id"]).count() == 0
    finally:
        db.close()

    accepted = client.post(
        f"/api/v1/capture/analyses/{body['qwen_analysis_id']}/review",
        headers=auth,
        json={"candidate_index": 0, "decision": "accept"},
    )
    assert accepted.status_code == 200, accepted.text
    accepted_body = accepted.json()
    assert accepted_body["written_to_lcr"] is True
    assert accepted_body["longitudinal_retrieval_status"] == "verified"
    assert accepted_body["assertion_id"]
    assert accepted_body["candidate"]["review_status"] == "accepted_by_caregiver"
    assert accepted_body["care_continuity_execution"]["source_caregiver_id"] == session["caregiver"]["id"]

    # Verify the decision through a separate authenticated read, not only the
    # POST receipt or the in-memory SQLAlchemy instance.
    persisted_review = client.get(
        f"/api/v1/capture/analyses/{body['qwen_analysis_id']}",
        headers=auth,
    )
    assert persisted_review.status_code == 200, persisted_review.text
    persisted_payload = persisted_review.json()
    assert persisted_payload["candidates"]["observations"][0]["review_status"] == "accepted_by_caregiver"
    assert persisted_payload["written_to_lcr"] is True
    assert persisted_payload["review_required"] is False

    repeated = client.post(
        f"/api/v1/capture/analyses/{body['qwen_analysis_id']}/review",
        headers=auth,
        json={"candidate_index": 0, "decision": "accept"},
    )
    assert repeated.status_code == 409, repeated.text

    current_care = client.get("/api/v1/care-continuity/current-care", headers=auth)
    assert current_care.status_code == 200, current_care.text
    assert any(
        item.get("id") == accepted_body["assertion_id"]
        and item.get("description") == "sleep: Mum slept poorly last night."
        for item in current_care.json().get("items", [])
    )

    db = TestSessionLocal()
    try:
        record = db.query(CaptureAnalysis).filter_by(id=body["qwen_analysis_id"]).one()
        assert record.source_text
        assert record.source_event_id == body["id"]
        assert record.execution_status["source_preservation"] == "verified"
        assert record.execution_status["evidence"] == "verified"
        assert record.execution_status["extraction"] == "available"
        assert record.execution_status["review"] == "completed"
        assert record.execution_status["reconciliation"] == "committed"
        assert record.execution_status["longitudinal_retrieval"] == "verified"
        assert record.candidates["observations"][0]["observation"] == "Mum slept badly last night."
        assert record.candidates["observations"][0]["corrected_observation"] == "Mum slept poorly last night."
        assert len(record.review_history) == 2
        assert record.review_history[0]["original_candidate"]["observation"] == "Mum slept badly last night."
        assert record.review_history[0]["reviewed_by_caregiver_id"] == session["caregiver"]["id"]
        assertion = db.query(CareAssertion).filter_by(id=accepted_body["assertion_id"]).one()
        assert assertion.source_event_id == body["id"]
        assert assertion.evidence_ids == body["evidence_ids"]
        assert assertion.created_by_caregiver_id == session["caregiver"]["id"]
        assert assertion.assertion_status == "caregiver_reviewed"
        assert assertion.asserted_value == "sleep: Mum slept poorly last night."
        assert source in assertion.source_text
    finally:
        db.close()


def test_daily_capture_rejection_never_creates_lcr_assertion_or_allows_retry(monkeypatch):
    client = TestClient(app)
    session = create_session(client, "Rejected care recipient")
    auth = {"Authorization": f"Bearer {session['access_token']}"}
    monkeypatch.setattr(api_v1_module, "extract_review_candidates", lambda *args, **kwargs: {
        "observations": [{
            "subject": "sleep",
            "observation": "Unverified generated suggestion.",
            "source_quote": "Original caregiver wording.",
            "review_status": "pending_review",
        }],
        "open_questions": [],
        "possible_safety_flags": [],
    })
    saved = client.post(
        "/api/v1/daily/log",
        headers=auth,
        json={
            "person_id": session["person"]["id"],
            "caregiver_id": session["caregiver"]["id"],
            "notes": "Original caregiver wording.",
            "medications": [],
            "vitals": [],
            "scales": [],
        },
    )
    assert saved.status_code == 200, saved.text
    analysis_id = saved.json()["qwen_analysis_id"]
    process_daily_capture_job(saved.json()["qwen_job_id"], monkeypatch)

    rejected = client.post(
        f"/api/v1/capture/analyses/{analysis_id}/review",
        headers=auth,
        json={"candidate_index": 0, "decision": "reject"},
    )
    assert rejected.status_code == 200, rejected.text
    assert rejected.json()["written_to_lcr"] is False
    assert rejected.json()["candidate"]["review_status"] == "rejected_by_caregiver"
    assert rejected.json()["assertion_id"] is None

    persisted_rejection = client.get(
        f"/api/v1/capture/analyses/{analysis_id}",
        headers=auth,
    )
    assert persisted_rejection.status_code == 200, persisted_rejection.text
    assert persisted_rejection.json()["candidates"]["observations"][0]["review_status"] == "rejected_by_caregiver"
    assert persisted_rejection.json()["written_to_lcr"] is False

    for decision in ("accept", "reject"):
        retry = client.post(
            f"/api/v1/capture/analyses/{analysis_id}/review",
            headers=auth,
            json={"candidate_index": 0, "decision": decision},
        )
        assert retry.status_code == 409, retry.text

    db = TestSessionLocal()
    try:
        assert db.query(CareAssertion).filter_by(person_id=session["person"]["id"]).count() == 0
        record = db.query(CaptureAnalysis).filter_by(id=analysis_id).one()
        assert record.candidates["observations"][0]["review_status"] == "rejected_by_caregiver"
        assert len(record.review_history) == 1
    finally:
        db.close()


def test_capture_review_rejects_foreign_analysis_and_invalid_candidate_index(monkeypatch):
    client = TestClient(app)
    first = create_session(client, "First care recipient")
    second = create_session(client, "Second care recipient")
    monkeypatch.setattr(api_v1_module, "extract_review_candidates", lambda *args, **kwargs: {
        "observations": [{
            "subject": "sleep",
            "observation": "Caregiver report about sleep.",
            "source_quote": "Caregiver report about sleep.",
            "review_status": "pending_review",
        }],
        "open_questions": [],
        "possible_safety_flags": [],
    })
    saved = client.post(
        "/api/v1/daily/log",
        headers={"Authorization": f"Bearer {first['access_token']}"},
        json={
            "person_id": first["person"]["id"],
            "caregiver_id": first["caregiver"]["id"],
            "notes": "Caregiver report about sleep.",
            "medications": [],
            "vitals": [],
            "scales": [],
        },
    )
    assert saved.status_code == 200, saved.text
    analysis_id = saved.json()["qwen_analysis_id"]
    process_daily_capture_job(saved.json()["qwen_job_id"], monkeypatch)
    second_auth = {"Authorization": f"Bearer {second['access_token']}"}

    foreign = client.post(
        f"/api/v1/capture/analyses/{analysis_id}/review",
        headers=second_auth,
        json={"candidate_index": 0, "decision": "accept"},
    )
    assert foreign.status_code == 404, foreign.text

    invalid_index = client.post(
        f"/api/v1/capture/analyses/{analysis_id}/review",
        headers={"Authorization": f"Bearer {first['access_token']}"},
        json={"candidate_index": 4, "decision": "accept"},
    )
    assert invalid_index.status_code == 404, invalid_index.text


def test_capture_review_rejects_repeat_decisions_and_empty_corrections(monkeypatch):
    client = TestClient(app)
    session = create_session(client, "Care recipient")
    monkeypatch.setattr(api_v1_module, "extract_review_candidates", lambda *args, **kwargs: {
        "observations": [{
            "subject": "sleep",
            "observation": "Caregiver report about sleep.",
            "source_quote": "Caregiver report about sleep.",
            "review_status": "pending_review",
        }],
        "open_questions": [],
        "possible_safety_flags": [],
    })
    saved = client.post(
        "/api/v1/daily/log",
        headers={"Authorization": f"Bearer {session['access_token']}"},
        json={
            "person_id": session["person"]["id"],
            "caregiver_id": session["caregiver"]["id"],
            "notes": "Caregiver report about sleep.",
            "medications": [],
            "vitals": [],
            "scales": [],
        },
    )
    assert saved.status_code == 200, saved.text
    analysis_id = saved.json()["qwen_analysis_id"]
    process_daily_capture_job(saved.json()["qwen_job_id"], monkeypatch)
    auth = {"Authorization": f"Bearer {session['access_token']}"}

    empty_correction = client.post(
        f"/api/v1/capture/analyses/{analysis_id}/review",
        headers=auth,
        json={"candidate_index": 0, "decision": "correct"},
    )
    assert empty_correction.status_code == 422, empty_correction.text

    accepted = client.post(
        f"/api/v1/capture/analyses/{analysis_id}/review",
        headers=auth,
        json={"candidate_index": 0, "decision": "accept"},
    )
    assert accepted.status_code == 200, accepted.text
    repeat = client.post(
        f"/api/v1/capture/analyses/{analysis_id}/review",
        headers=auth,
        json={"candidate_index": 0, "decision": "reject"},
    )
    assert repeat.status_code == 409, repeat.text

def test_daily_capture_cannot_promote_model_observation_not_supported_by_source_quote(monkeypatch):
    client = TestClient(app)
    session = create_session(client, "Source validation recipient")
    auth = {"Authorization": f"Bearer {session['access_token']}"}
    source = "Caregiver report: Mum slept badly last night."
    monkeypatch.setattr(api_v1_module, "extract_review_candidates", lambda *args, **kwargs: {
        "observations": [{
            "subject": "sleep",
            "observation": "Mum has severe dementia caused by poor sleep.",
            "source_quote": "Mum slept badly last night.",
            "review_status": "pending_review",
        }],
        "open_questions": [],
        "possible_safety_flags": [],
    })

    saved = client.post(
        "/api/v1/daily/log",
        headers=auth,
        json={
            "person_id": session["person"]["id"],
            "caregiver_id": session["caregiver"]["id"],
            "notes": source,
            "medications": [],
            "vitals": [],
            "scales": [],
        },
    )
    assert saved.status_code == 200, saved.text
    analysis_id = saved.json()["qwen_analysis_id"]
    process_daily_capture_job(saved.json()["qwen_job_id"], monkeypatch)

    accepted = client.post(
        f"/api/v1/capture/analyses/{analysis_id}/review",
        headers=auth,
        json={"candidate_index": 0, "decision": "accept"},
    )
    assert accepted.status_code == 409, accepted.text
    assert "not supported by its preserved source passage" in accepted.json()["detail"]
    assert accepted.json()["detail"].find("no LCR assertion") >= 0

    db = TestSessionLocal()
    try:
        assert db.query(CareAssertion).filter_by(person_id=session["person"]["id"]).count() == 0
        record = db.query(CaptureAnalysis).filter_by(id=analysis_id).one()
        assert record.candidates["observations"][0]["review_status"] == "pending_review"
    finally:
        db.close()


def test_daily_capture_extracts_from_saved_evidence_and_persists_execution_stages(monkeypatch):
    client = TestClient(app)
    session = create_session(client, "Execution stage recipient")
    auth = {"Authorization": f"Bearer {session['access_token']}"}
    quote = "Mum slept badly last night."
    seen = []

    def extract(source_text, *, source_event_id, source_evidence_id=None):
        seen.append((source_text, source_event_id, source_evidence_id))
        return {
            "observations": [{
                "subject": "sleep",
                "observation": quote,
                "source_quote": quote,
                "review_status": "pending_review",
            }],
            "open_questions": [],
            "possible_safety_flags": [],
        }

    monkeypatch.setattr(api_v1_module, "extract_review_candidates", extract)
    saved = client.post(
        "/api/v1/daily/log",
        headers=auth,
        json={
            "person_id": session["person"]["id"],
            "caregiver_id": session["caregiver"]["id"],
            "notes": f"Caregiver report: {quote}",
            "medications": [],
            "vitals": [],
            "scales": [],
        },
    )
    assert saved.status_code == 200, saved.text
    body = saved.json()
    assert body["qwen_analysis_id"]
    assert body["qwen_extraction_status"] == "queued"
    process_daily_capture_job(body["qwen_job_id"], monkeypatch)

    db = TestSessionLocal()
    try:
        evidence = db.query(Evidence).filter_by(id=body["evidence_ids"][0]).one()
        analysis = db.query(CaptureAnalysis).filter_by(id=body["qwen_analysis_id"]).one()
        assert seen == [(analysis.source_text, body["id"], evidence.id)]
        assert analysis.source_text
        assert analysis.execution_status["source_preservation"] == "verified"
        assert analysis.execution_status["evidence"] == "verified"
        assert analysis.execution_status["extraction"] == "available"
        assert analysis.execution_status["assessment"] == "available"
        assert analysis.execution_status["review"] == "required"
        assert analysis.execution_status["reconciliation"] == "completed"
        assert analysis.execution_status["longitudinal_retrieval"] == "verified"
    finally:
        db.close()


def test_daily_capture_does_not_report_longitudinal_processing_complete_when_delta_fails(monkeypatch):
    import json

    client = TestClient(app)
    session = create_session(client, "Delta failure recipient")
    auth = {"Authorization": f"Bearer {session['access_token']}"}

    extraction_calls = {"count": 0}

    def extract_candidates_once(*args, **kwargs):
        extraction_calls["count"] += 1
        return {
            "observations": [],
            "open_questions": [],
            "possible_safety_flags": [],
        }

    monkeypatch.setattr(api_v1_module, "extract_review_candidates", extract_candidates_once)
    from app.services import qwen_background_worker

    delta_results = iter([
        {"status": "failed", "error_code": "delta_store_unavailable"},
        {"status": "completed", "run_id": "delta-retry-run-1", "reused": False},
    ])
    monkeypatch.setattr(
        qwen_background_worker,
        "_reconcile_daily_capture_delta",
        lambda **kwargs: next(delta_results),
    )

    saved = client.post(
        "/api/v1/daily/log",
        headers=auth,
        json={
            "person_id": session["person"]["id"],
            "caregiver_id": session["caregiver"]["id"],
            "notes": "Caregiver reports a change in sleep.",
            "medications": [],
            "vitals": [],
            "scales": [],
        },
    )
    assert saved.status_code == 200, saved.text
    body = saved.json()
    process_daily_capture_job(body["qwen_job_id"], monkeypatch)

    db = TestSessionLocal()
    try:
        analysis = db.query(CaptureAnalysis).filter_by(id=body["qwen_analysis_id"]).one()
        assert analysis.execution_status["reconciliation"] == "completed"
        assert analysis.execution_status["delta_reconciliation"] == "failed"
        assert analysis.execution_status["longitudinal_processing"] == "retrying"
        assert analysis.execution_status["longitudinal_processing_error"] == "delta_store_unavailable"

        from app.models.qwen_background_job import QwenBackgroundJob
        job = db.query(QwenBackgroundJob).filter_by(id=body["qwen_job_id"]).one()
        assert job.workflow == "daily_capture_delta_reconciliation"
        assert job.status == "queued"
        result = json.loads(job.result_text)
        assert result["reconciliation_status"] == "completed"
        assert result["delta_reconciliation_status"] == "failed"
        assert result["delta_engine_output_persisted"] is False
        assert result["longitudinal_processing_status"] == "retrying"
        assert result["longitudinal_processing_error"] == "delta_store_unavailable"
        event_ids_before_retry = db.query(CareEvent.id).filter_by(
            person_id=session["person"]["id"]
        ).all()
        evidence_ids_before_retry = db.query(Evidence.id).filter_by(
            person_id=session["person"]["id"]
        ).all()
        assert extraction_calls["count"] == 1
    finally:
        db.close()

    # The retry reuses the persisted source event/evidence and runs Delta only.
    assert process_daily_capture_job(body["qwen_job_id"], monkeypatch) is True
    db = TestSessionLocal()
    try:
        analysis = db.query(CaptureAnalysis).filter_by(id=body["qwen_analysis_id"]).one()
        job = db.query(QwenBackgroundJob).filter_by(id=body["qwen_job_id"]).one()
        assert job.workflow == "daily_capture_delta_reconciliation"
        assert job.status == "completed"
        assert analysis.execution_status["delta_reconciliation"] == "completed"
        assert analysis.execution_status["longitudinal_processing"] == "completed"
        assert analysis.execution_status["delta_run_id"] == "delta-retry-run-1"
        assert extraction_calls["count"] == 1
        assert db.query(CareEvent.id).filter_by(person_id=session["person"]["id"]).all() == event_ids_before_retry
        assert db.query(Evidence.id).filter_by(person_id=session["person"]["id"]).all() == evidence_ids_before_retry
    finally:
        db.close()



def test_capture_candidate_review_requires_correct_records_permission():
    client = TestClient(app)
    session = create_session(client, "Restricted care recipient")
    headers = {"Authorization": f"Bearer {session['access_token']}"}
    db = TestSessionLocal()
    try:
        grant = db.query(CaregiverAccessGrant).filter_by(
            person_id=session["person"]["id"],
            caregiver_id=session["caregiver"]["id"],
        ).one()
        grant.can_manage_members = False
        grant.permissions = ["view_lcr"]
        db.commit()
    finally:
        db.close()

    denied_review = client.post(
        "/api/v1/capture/analyses/nonexistent-analysis/review",
        headers=headers,
        json={"candidate_index": 0, "decision": "reject"},
    )
    assert denied_review.status_code == 403, denied_review.text
    assert "correct records" in denied_review.text.lower()

    denied_read = client.get(
        "/api/v1/capture/analyses/nonexistent-analysis",
        headers=headers,
    )
    assert denied_read.status_code == 404, denied_read.text



def test_second_authorized_caregiver_can_review_first_caregivers_daily_capture(monkeypatch):
    client = TestClient(app)
    first = create_session(client, "Shared care recipient")
    first_auth = {"Authorization": f"Bearer {first['access_token']}"}
    source = "Caregiver report: the person prefers a quiet room after lunch."
    def deterministic_extraction(source_text, *, source_event_id, source_evidence_id=None):
        return {
            "observations": [{
                "subject": "environment",
                "observation": "the person prefers a quiet room after lunch.",
                "source_quote": "the person prefers a quiet room after lunch.",
                "source_event_id": source_event_id,
                "source_evidence_id": source_evidence_id,
                "review_status": "pending_review",
            }],
            "open_questions": [],
            "possible_safety_flags": [],
        }
    saved = client.post(
        "/api/v1/daily/log",
        headers=first_auth,
        json={
            "person_id": first["person"]["id"],
            "caregiver_id": first["caregiver"]["id"],
            "notes": source,
            "medications": [],
            "vitals": [],
            "scales": [],
        },
    )
    assert saved.status_code == 200, saved.text
    body = saved.json()
    process_daily_capture_job(body["qwen_job_id"], monkeypatch, extractor=deterministic_extraction)

    invitation = client.post(
        "/api/v1/caregiver-access/invitations",
        headers=first_auth,
        json={"email": "shared-reviewer@example.com", "expires_in_days": 7},
    )
    assert invitation.status_code == 201, invitation.text
    accepted = client.post(
        "/api/v1/caregiver-access/invitations/accept",
        json={
            "token": invitation.json()["invitation_token"],
            "email": "shared-reviewer@example.com",
            "password": "another-long-password-789",
            "name": "Second caregiver",
        },
    )
    assert accepted.status_code == 201, accepted.text
    second_auth = {"Authorization": f"Bearer {accepted.json()['access_token']}"}

    db = TestSessionLocal()
    try:
        grant = db.query(CaregiverAccessGrant).filter_by(
            person_id=first["person"]["id"],
            caregiver_id=accepted.json()["caregiver_id"],
        ).one()
        grant.can_manage_members = False
        grant.permissions = list(grant.permissions or []) + ["correct_records"]
        db.commit()
    finally:
        db.close()

    visible = client.get(
        f"/api/v1/capture/analyses/{body['qwen_analysis_id']}",
        headers=second_auth,
    )
    assert visible.status_code == 200, visible.text

    reviewed = client.post(
        f"/api/v1/capture/analyses/{body['qwen_analysis_id']}/review",
        headers=second_auth,
        json={"candidate_index": 0, "decision": "accept"},
    )
    assert reviewed.status_code == 200, reviewed.text
    result = reviewed.json()
    assert result["written_to_lcr"] is True
    assert result["candidate"]["accepted_by_caregiver_id"] == accepted.json()["caregiver_id"]

    db = TestSessionLocal()
    try:
        source_event = db.query(CareEvent).filter_by(id=body["id"]).one()
        source_evidence = db.query(Evidence).filter_by(id=body["evidence_ids"][0]).one()
        assertion = db.query(CareAssertion).filter_by(id=result["assertion_id"]).one()
        assert source_event.created_by_caregiver_id == first["caregiver"]["id"]
        assert source_evidence.reported_by_caregiver_id == first["caregiver"]["id"]
        assert assertion.created_by_caregiver_id == accepted.json()["caregiver_id"]
        assert assertion.source_event_id == source_event.id
        assert assertion.evidence_ids == [source_evidence.id]
    finally:
        db.close()
