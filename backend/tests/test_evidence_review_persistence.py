import hashlib
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.database import Base, get_db
from app.main import app
from app.models.anonymous_session import AnonymousSession
from app.models.care import Caregiver, Evidence, Person
from app.models.care_continuity import EvidenceReviewRecord, EvidenceClaimAssessment
from app.api.care_continuity import _near_duplicate_candidates

engine = create_engine(
    "sqlite:///:memory:",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def override_get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def issue_client(person_id: str) -> TestClient:
    db = SessionLocal()
    caregiver = db.query(Caregiver).filter(Caregiver.person_id == person_id).first()
    assert caregiver is not None
    token = f"evidence-review-token-{person_id}"
    db.add(AnonymousSession(
        person_id=person_id,
        caregiver_id=caregiver.id,
        token_hash=hashlib.sha256(token.encode()).hexdigest(),
        expires_at=datetime.now(timezone.utc) + timedelta(hours=2),
    ))
    db.commit()
    db.close()
    client = TestClient(app)
    client.headers.update({"Authorization": f"Bearer {token}"})
    return client


@pytest.fixture
def review_records():
    # This fixture uses a shared in-memory SQLite connection; always reset it,
    # including after setup failures, so test order cannot leak records.
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    app.dependency_overrides[get_db] = override_get_db
    db = SessionLocal()
    try:
        person_a = Person(id="review-person-a", name="Person A")
        caregiver_a = Caregiver(
            id="review-caregiver-a", name="Caregiver A", email="review-a@example.com",
            relationship="Family", person_id=person_a.id,
        )
        person_b = Person(id="review-person-b", name="Person B")
        caregiver_b = Caregiver(
            id="review-caregiver-b", name="Caregiver B", email="review-b@example.com",
            relationship="Family", person_id=person_b.id,
        )
        db.add_all([person_a, caregiver_a, person_b, caregiver_b])
        db.flush()
        person_a_id = person_a.id
        person_b_id = person_b.id
        evidence_a = Evidence(
            id="review-evidence-a", person_id=person_a_id, type="caregiver_report",
            source_text="Caregiver reports that the appointment occurred.",
            uploaded_by_caregiver_id=caregiver_a.id, source_type="caregiver_report",
            time_provenance="reported_by_caregiver",
        )
        evidence_b = Evidence(
            id="review-evidence-b", person_id=person_b_id, type="caregiver_report",
            source_text="Separate care record evidence.",
            uploaded_by_caregiver_id=caregiver_b.id, source_type="caregiver_report",
        )
        db.add_all([evidence_a, evidence_b])
        db.commit()
    finally:
        db.close()
    try:
        yield issue_client(person_a_id), issue_client(person_b_id)
    finally:
        app.dependency_overrides.clear()
        Base.metadata.drop_all(bind=engine)


CRITERIA = [
    {"criterion": "source attribution", "result": "present", "rationale": "Reporter identity is recorded."},
    {"criterion": "independent corroboration", "result": "none_linked", "rationale": "No independent source is linked."},
    {"criterion": "temporal context", "result": "specified", "rationale": "The reported timing is recorded."},
    {"criterion": "evidence freshness", "result": "undetermined", "rationale": "No domain-specific review interval is configured."},
    {"criterion": "record completeness", "result": "partial", "rationale": "Only the report is present."},
]


def test_evidence_review_create_retrieve_is_persisted_and_person_scoped(review_records):
    client_a, client_b = review_records
    payload = {
        "review_type": "reliability",
        "outcome": "unverified",
        "rationale": "A caregiver report is attributed, but independent confirmation has not been reviewed.",
        "policy_version": "evidence-review-v1",
        "criteria": CRITERIA,
    }
    created = client_a.post("/api/v1/care-continuity/evidence/review-evidence-a/reviews", json=payload)
    assert created.status_code == 201, created.text
    saved = created.json()
    assert saved["evidence_id"] == "review-evidence-a"
    assert saved["person_id"] == "review-person-a"
    assert saved["source_attribution"]["source_type"] == "caregiver_report"
    assert saved["source_attribution"]["uploaded_by_caregiver_id"] == "review-caregiver-a"

    retrieved = client_a.get("/api/v1/care-continuity/evidence/review-evidence-a/reviews")
    assert retrieved.status_code == 200, retrieved.text
    assert [item["id"] for item in retrieved.json()] == [saved["id"]]

    assert client_b.get("/api/v1/care-continuity/evidence/review-evidence-a/reviews").status_code == 404
    assert client_b.post("/api/v1/care-continuity/evidence/review-evidence-a/reviews", json=payload).status_code == 404


def test_evidence_review_history_cannot_be_updated_or_deleted(review_records):
    client_a, _ = review_records
    response = client_a.post("/api/v1/care-continuity/evidence/review-evidence-a/reviews", json={
        "review_type": "quality",
        "outcome": "limited",
        "rationale": "The source is a single caregiver report.",
        "policy_version": "evidence-review-v1",
        "criteria": CRITERIA,
    })
    assert response.status_code == 201, response.text
    db = SessionLocal()
    row = db.query(EvidenceReviewRecord).filter(EvidenceReviewRecord.id == response.json()["id"]).one()
    row.rationale = "Edited after the fact"
    with pytest.raises(ValueError, match="append-only"):
        db.commit()
    db.rollback()
    db.delete(row)
    with pytest.raises(ValueError, match="append-only"):
        db.commit()
    db.rollback()
    db.close()



def test_source_assertion_preserves_reported_status_evidence_and_event_time(review_records):
    client_a, _ = review_records
    response = client_a.post("/api/v1/care-continuity/assertions", json={
        "person_id": "review-person-b",
        "subject_type": "appointment",
        "subject_id": "appointment-42",
        "field": "attendance",
        "assertion_kind": "time_bound_observation",
        "asserted_value": "Caregiver reported attendance",
        "source_label": "Caregiver report",
        "source_text": "The caregiver said the appointment was attended.",
        "evidence_ids": ["review-evidence-a"],
        "observed_at": "2026-10-28T09:30:00Z",
        "review_after_days": 14,
        "assertion_status": "confirmed",
    })
    assert response.status_code == 201, response.text
    created = response.json()
    assert created["person_id"] == "review-person-a"
    assert created["assertion_status"] == "reported"
    assert created["evidence_ids"] == ["review-evidence-a"]
    assert created["observed_at"].startswith("2026-10-28T09:30:00")

    retrieved = client_a.get("/api/v1/care-continuity/assertions?limit=100")
    assert retrieved.status_code == 200, retrieved.text
    persisted = [item for item in retrieved.json() if item["id"] == created["id"]]
    assert len(persisted) == 1
    assert persisted[0]["asserted_value"] == "Caregiver reported attendance"
    assert persisted[0]["evidence_ids"] == ["review-evidence-a"]


def test_time_bound_assertion_requires_actual_event_time(review_records):
    client_a, _ = review_records
    response = client_a.post("/api/v1/care-continuity/assertions", json={
        "subject_type": "daily_care",
        "subject_id": "meal-1",
        "field": "appetite",
        "assertion_kind": "time_bound_observation",
        "asserted_value": "Reduced appetite",
        "source_label": "Caregiver report",
        "evidence_ids": [],
        "review_after_days": 7,
    })
    assert response.status_code == 422
    assert "actual event time" in response.json()["detail"]


def test_evidence_capture_preserves_distinct_provenance_and_hash(review_records):
    import hashlib

    client_a, _ = review_records
    source_text = "Original caregiver note: two awakenings were recorded."
    response = client_a.post("/api/v1/evidence", json={
        "person_id": "review-person-a",
        "type": "caregiver_report",
        "evidence_kind": "caregiver_report",
        "epistemic_status": "unverified",
        "source_text": source_text,
        "uploaded_by_caregiver_id": "review-caregiver-a",
        "original_author": "Caregiver's handwritten note",
        "reported_by_label": "Caregiver A",
        "reported_by_caregiver_id": "review-caregiver-a",
        "collected_by_caregiver_id": "review-caregiver-a",
        "collected_at": "2026-10-29T08:00:00Z",
        "occurred_at": "2026-10-28T22:00:00Z",
        "occurred_at_timezone": "America/New_York",
        "origin_channel": "manual_entry",
        "original_source_reference": "family-sleep-log/2026-10-28",
        "parent_evidence_id": "review-evidence-a",
    })
    assert response.status_code == 200, response.text
    saved = response.json()
    assert saved["source_text"] == source_text
    assert saved["evidence_kind"] == "caregiver_report"
    assert saved["epistemic_status"] == "unverified"
    assert saved["source_text_sha256"] == hashlib.sha256(source_text.encode("utf-8")).hexdigest()
    assert saved["ingested_at"]
    assert saved["uploaded_by_caregiver_id"] == "review-caregiver-a"
    assert saved["reported_by_caregiver_id"] == "review-caregiver-a"
    assert saved["collected_by_caregiver_id"] == "review-caregiver-a"
    assert saved["original_author"] == "Caregiver's handwritten note"
    assert saved["occurred_at"].startswith("2026-10-28T22:00:00")
    assert saved["occurred_at_timezone"] == "America/New_York"
    assert saved["parent_evidence_id"] == "review-evidence-a"


def test_evidence_provenance_rejects_reporter_from_another_care_record(review_records):
    client_a, _ = review_records
    response = client_a.post("/api/v1/evidence", json={
        "person_id": "review-person-a",
        "type": "caregiver_report",
        "source_text": "A report with a foreign reporter identity.",
        "uploaded_by_caregiver_id": "review-caregiver-a",
        "reported_by_caregiver_id": "review-caregiver-b",
    })
    assert response.status_code == 422, response.text
    assert "reported_by_caregiver_id" in response.text


def test_evidence_creation_requires_server_authorized_record_identity(review_records):
    from fastapi.testclient import TestClient

    client_a, _ = review_records
    payload = {
        "person_id": "review-person-a",
        "type": "caregiver_report",
        "source_text": "Evidence must be bound to the authorized session.",
        "uploaded_by_caregiver_id": "review-caregiver-a",
    }
    unauthenticated = TestClient(app)
    assert unauthenticated.post("/api/v1/evidence", json=payload).status_code == 401

    # A valid session for person A cannot create evidence in person B's record,
    # even when the request body supplies a valid caregiver B identity.
    foreign_payload = {
        **payload,
        "person_id": "review-person-b",
        "uploaded_by_caregiver_id": "review-caregiver-b",
    }
    assert client_a.post("/api/v1/evidence", json=foreign_payload).status_code == 403


def test_evidence_kind_rejects_unregistered_classifications(review_records):
    client_a, _ = review_records
    response = client_a.post("/api/v1/evidence", json={
        "person_id": "review-person-a",
        "type": "caregiver_report",
        "evidence_kind": "trusted_verified_fact",
        "source_text": "An unsupported kind must not be silently accepted.",
        "uploaded_by_caregiver_id": "review-caregiver-a",
    })
    assert response.status_code == 422, response.text


def test_source_text_integrity_endpoint_is_person_scoped_and_reports_exact_text_hash(review_records):
    client_a, client_b = review_records
    response = client_a.get("/api/v1/care-continuity/evidence/review-evidence-a/integrity")
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["evidence_id"] == "review-evidence-a"
    assert payload["person_id"] == "review-person-a"
    assert payload["source_text_integrity_status"] == "match"
    assert payload["stored_source_text_sha256"] == hashlib.sha256(
        b"Caregiver reports that the appointment occurred."
    ).hexdigest()
    assert payload["hash_scope"] == "exact UTF-8 encoding of the currently stored source_text field"
    assert payload["original_file_authenticity_verified"] is False

    foreign = client_b.get("/api/v1/care-continuity/evidence/review-evidence-a/integrity")
    assert foreign.status_code == 404


def test_original_evidence_source_text_and_hash_cannot_be_changed_through_orm(review_records):
    db = SessionLocal()
    try:
        evidence = db.query(Evidence).filter(Evidence.id == "review-evidence-a").one()
        original_text = evidence.source_text
        original_hash = evidence.source_text_sha256
        evidence.source_text = "Changed text must not overwrite the original."
        with pytest.raises(ValueError, match="immutable"):
            db.commit()
        db.rollback()

        restored = db.query(Evidence).filter(Evidence.id == "review-evidence-a").one()
        assert restored.source_text == original_text
        assert restored.source_text_sha256 == original_hash
    finally:
        db.close()


def test_claim_assessments_require_exact_source_passages_and_preserve_version_history(review_records):
    client_a, client_b = review_records
    claim_response = client_a.post("/api/v1/care-continuity/assertions", json={
        "subject_type": "appointment",
        "subject_id": "appointment-evidence-test",
        "field": "attendance",
        "assertion_kind": "time_bound_observation",
        "observed_at": "2026-10-28T09:30:00Z",
        "asserted_value": "The appointment occurred.",
        "source_label": "Caregiver report",
        "source_text": "Caregiver reports that the appointment occurred.",
        "evidence_ids": ["review-evidence-a"],
    })
    assert claim_response.status_code == 201, claim_response.text
    claim_id = claim_response.json()["id"]

    payload = {
        "status": "supported",
        "rationale": "The linked source passage explicitly supports the narrow attendance claim; this does not independently verify the reporter.",
        "policy_version": "claim-assessment-v1",
        "evidence_links": [{
            "evidence_id": "review-evidence-a",
            "relationship": "supports",
            "source_passage": "Caregiver reports that the appointment occurred.",
            "scope_note": "Supports what was reported, not independent attendance verification.",
        }],
    }
    created = client_a.post(f"/api/v1/care-continuity/claims/{claim_id}/assessments", json=payload)
    assert created.status_code == 201, created.text
    first = created.json()
    assert first["status"] == "supported"
    assert first["claim_version"] == 1
    assert first["reviewed_by_caregiver_id"] == "review-caregiver-a"
    assert first["evidence_links"][0]["source_passage"] == "Caregiver reports that the appointment occurred."

    bad_passage = {
        **payload,
        "evidence_links": [{
            "evidence_id": "review-evidence-a",
            "relationship": "supports",
            "source_passage": "The source says something else.",
        }],
    }
    rejected = client_a.post(f"/api/v1/care-continuity/claims/{claim_id}/assessments", json=bad_passage)
    assert rejected.status_code == 422, rejected.text

    second_payload = {
        **payload,
        "status": "partially_supported",
        "rationale": "The passage supports only that the caregiver reported attendance; it does not independently establish attendance.",
        "evidence_links": [{
            "evidence_id": "review-evidence-a",
            "relationship": "scope_limited",
            "source_passage": "Caregiver reports that the appointment occurred.",
            "scope_note": "Reporter attribution is supported; the underlying event is not independently confirmed.",
        }],
    }
    second_response = client_a.post(f"/api/v1/care-continuity/claims/{claim_id}/assessments", json=second_payload)
    assert second_response.status_code == 201, second_response.text
    second = second_response.json()
    assert second["claim_version"] == 2
    assert second["supersedes_assessment_id"] == first["id"]

    replacement_claim_response = client_a.post("/api/v1/care-continuity/assertions", json={
        "subject_type": "appointment",
        "subject_id": "appointment-evidence-test",
        "field": "attendance",
        "assertion_kind": "current_state",
        "review_after_days": 30,
        "asserted_value": "Attendance has not been independently confirmed.",
        "source_label": "Review note",
        "source_text": "Assessment remains uncertain.",
        "evidence_ids": [],
    })
    assert replacement_claim_response.status_code == 201, replacement_claim_response.text
    replacement_claim_id = replacement_claim_response.json()["id"]
    superseded_payload = {
        "status": "superseded",
        "rationale": "A newer claim now represents the current scope of this assertion.",
        "policy_version": "claim-assessment-v1",
        "superseded_by_claim_id": replacement_claim_id,
        "evidence_links": [],
    }
    superseded_response = client_a.post(
        f"/api/v1/care-continuity/claims/{claim_id}/assessments",
        json=superseded_payload,
    )
    assert superseded_response.status_code == 201, superseded_response.text
    third = superseded_response.json()
    assert third["claim_version"] == 3
    assert third["status"] == "superseded"
    assert third["superseded_by_claim_id"] == replacement_claim_id

    cycle = client_a.post(
        f"/api/v1/care-continuity/claims/{replacement_claim_id}/assessments",
        json={
            "status": "superseded",
            "rationale": "This would create a cycle and must be rejected.",
            "policy_version": "claim-assessment-v1",
            "superseded_by_claim_id": claim_id,
            "evidence_links": [],
        },
    )
    assert cycle.status_code == 422, cycle.text

    history = client_a.get(f"/api/v1/care-continuity/claims/{claim_id}/assessments")
    assert history.status_code == 200, history.text
    assert [item["id"] for item in history.json()] == [first["id"], second["id"], third["id"]]
    assert client_b.get(f"/api/v1/care-continuity/claims/{claim_id}/assessments").status_code == 404

    db = SessionLocal()
    try:
        saved = db.query(EvidenceClaimAssessment).filter(EvidenceClaimAssessment.id == first["id"]).one()
        saved.rationale = "Attempted history rewrite"
        with pytest.raises(ValueError, match="append-only"):
            db.commit()
        db.rollback()
    finally:
        db.close()


def test_source_relationships_persist_lineage_and_reject_cross_record_or_cycle(review_records):
    client_a, client_b = review_records
    # Add a second evidence item to person A; person B already owns review-evidence-b.
    db = SessionLocal()
    try:
        caregiver = db.query(Caregiver).filter(Caregiver.person_id == "review-person-a").one()
        db.add(Evidence(
            id="review-evidence-a-copy", person_id="review-person-a", type="forwarded_message",
            source_text="Forwarded copy of the original appointment message.",
            uploaded_by_caregiver_id=caregiver.id, source_type="imported",
        ))
        db.commit()
    finally:
        db.close()

    created = client_a.post("/api/v1/care-continuity/evidence/review-evidence-a-copy/relationships", json={
        "related_evidence_id": "review-evidence-a",
        "relationship_type": "forwarded_from",
        "source_independence_status": "shared_origin",
        "origin_group_key": "appointment-message-1",
        "rationale": "This record was forwarded from the original message.",
    })
    assert created.status_code == 201, created.text
    assert created.json()["reviewed_by_caregiver_id"] == "review-caregiver-a"
    assert created.json()["source_independence_status"] == "shared_origin"

    listed = client_a.get("/api/v1/care-continuity/evidence/review-evidence-a/relationships")
    assert listed.status_code == 200, listed.text
    assert len(listed.json()) == 1
    assert listed.json()[0]["source_evidence_id"] == "review-evidence-a-copy"

    cross_record = client_a.post("/api/v1/care-continuity/evidence/review-evidence-a/relationships", json={
        "related_evidence_id": "review-evidence-b",
        "relationship_type": "corroborates",
        "source_independence_status": "independent",
        "rationale": "Must not cross care records.",
    })
    assert cross_record.status_code == 404

    cycle = client_a.post("/api/v1/care-continuity/evidence/review-evidence-a/relationships", json={
        "related_evidence_id": "review-evidence-a-copy",
        "relationship_type": "derived_from",
        "source_independence_status": "shared_origin",
        "rationale": "Would make a cycle in the source lineage graph.",
    })
    assert cycle.status_code == 409

    duplicate = client_a.post("/api/v1/care-continuity/evidence/review-evidence-a-copy/relationships", json={
        "related_evidence_id": "review-evidence-a",
        "relationship_type": "forwarded_from",
        "source_independence_status": "shared_origin",
        "origin_group_key": "appointment-message-1",
        "rationale": "Duplicate relationship.",
    })
    assert duplicate.status_code == 409


def test_source_relationship_append_only_guard(review_records):
    client_a, _ = review_records
    db = SessionLocal()
    try:
        caregiver = db.query(Caregiver).filter(Caregiver.person_id == "review-person-a").one()
        db.add(Evidence(
            id="review-evidence-lineage-guard", person_id="review-person-a", type="derived",
            source_text="Derived evidence item for append-only guard test.",
            uploaded_by_caregiver_id=caregiver.id, source_type="derived",
        ))
        db.flush()
        from app.models.care_continuity import EvidenceSourceRelationship
        relationship = EvidenceSourceRelationship(
            person_id="review-person-a",
            source_evidence_id="review-evidence-lineage-guard",
            related_evidence_id="review-evidence-a",
            relationship_type="derived_from",
            source_independence_status="shared_origin",
            rationale="Derived from the original source.",
            reviewed_by_caregiver_id=caregiver.id,
        )
        db.add(relationship)
        db.commit()
        relationship.rationale = "Attempted history rewrite"
        with pytest.raises(ValueError, match="append-only"):
            db.commit()
        db.rollback()
    finally:
        db.close()

def test_structured_observation_requires_exact_source_span_and_preserves_epistemic_kind(review_records):
    client_a, client_b = review_records
    source_text = "Caregiver reports three awakenings overnight."
    db = SessionLocal()
    try:
        caregiver = db.query(Caregiver).filter(Caregiver.person_id == "review-person-a").one()
        db.add(Evidence(
            id="review-observation-evidence", person_id="review-person-a", type="caregiver_report",
            source_text=source_text, uploaded_by_caregiver_id=caregiver.id, source_type="caregiver_report",
        ))
        db.commit()
    finally:
        db.close()

    payload = {
        "observation_kind": "caregiver_report",
        "epistemic_status": "reported",
        "subject_label": "care recipient",
        "attribute": "sleep_awakenings",
        "value_text": "three awakenings",
        "source_passage": source_text,
        "source_span_start": 0,
        "source_span_end": len(source_text),
        "extraction_method": "human_entered",
        "extraction_version": "manual-v1",
    }
    created = client_a.post("/api/v1/care-continuity/evidence/review-observation-evidence/observations", json=payload)
    assert created.status_code == 201, created.text
    row = created.json()
    assert row["epistemic_status"] == "reported"
    assert row["observation_kind"] == "caregiver_report"
    assert source_text[row["source_span_start"]:row["source_span_end"]] == row["source_passage"]
    assert row["created_by_caregiver_id"] == "review-caregiver-a"

    correction = dict(
        payload,
        value_text="three awakenings",
        supersedes_observation_id=row["id"],
        correction_rationale="The source span was reviewed and this observation was re-entered as a new version.",
    )
    corrected = client_a.post(
        "/api/v1/care-continuity/evidence/review-observation-evidence/observations", json=correction
    )
    assert corrected.status_code == 201, corrected.text
    assert corrected.json()["supersedes_observation_id"] == row["id"]
    assert corrected.json()["correction_rationale"] == correction["correction_rationale"]

    invalid_passage = dict(payload, source_passage="The person slept well.")
    assert client_a.post("/api/v1/care-continuity/evidence/review-observation-evidence/observations", json=invalid_passage).status_code == 422

    invented_unknown = dict(payload, epistemic_status="unknown", value_text="three awakenings")
    assert client_a.post("/api/v1/care-continuity/evidence/review-observation-evidence/observations", json=invented_unknown).status_code == 422

    unqualified_observation = dict(payload, observation_kind="direct_observation", epistemic_status="observed")
    assert client_a.post("/api/v1/care-continuity/evidence/review-observation-evidence/observations", json=unqualified_observation).status_code == 422

    listed = client_a.get("/api/v1/care-continuity/evidence/review-observation-evidence/observations")
    assert listed.status_code == 200, listed.text
    history = listed.json()
    assert {item["id"] for item in history} == {row["id"], corrected.json()["id"]}
    corrected_history_row = next(item for item in history if item["id"] == corrected.json()["id"])
    assert corrected_history_row["supersedes_observation_id"] == row["id"]
    assert client_b.get("/api/v1/care-continuity/evidence/review-observation-evidence/observations").status_code == 404


def test_near_duplicate_candidates_are_bounded_and_never_claim_common_origin():
    source = (
        "Caregiver recorded that the person woke three times overnight and needed "
        "reassurance before returning to sleep. The note describes the same evening "
        "routine and says daytime tiredness was reported the following morning."
    )
    near = source.replace("three times", "3 times")
    result = _near_duplicate_candidates([
        SimpleNamespace(id="source-a", source_text=source),
        SimpleNamespace(id="source-b", source_text=near),
        SimpleNamespace(id="short", source_text="same short note"),
    ])
    assert result["status"] == "completed"
    assert result["algorithm_version"] == "normalized-text-sequencematcher-v1"
    assert result["candidate_groups"]
    assert result["candidate_groups"][0]["evidence_ids"] == ["source-a", "source-b"]
    assert "does not establish" in result["interpretation_limit"]
    assert "independent" in result["candidate_groups"][0]["interpretation"]
    assert all("confidence" not in candidate for candidate in result["candidate_groups"])


def test_exact_normalized_duplicates_are_not_repeated_as_near_duplicates():
    source = "The caregiver recorded a detailed note about the person's routine and support needs. " * 2
    result = _near_duplicate_candidates([
        SimpleNamespace(id="exact-a", source_text=source),
        SimpleNamespace(id="exact-b", source_text="  " + source.upper().replace("  ", " ") + "  "),
    ])
    assert result["status"] == "completed"
    assert result["candidate_groups"] == []


def test_near_duplicate_candidates_fail_closed_when_comparison_limit_is_exceeded():
    from app.api.care_continuity import NEAR_DUPLICATE_MAX_RECORDS

    rows = [
        SimpleNamespace(id=f"evidence-{index}", source_text="A sufficiently long source note " * 5)
        for index in range(NEAR_DUPLICATE_MAX_RECORDS + 1)
    ]
    result = _near_duplicate_candidates(rows)
    assert result["status"] == "not_run_record_limit"
    assert result["candidate_groups"] == []
    assert result["records_available"] == NEAR_DUPLICATE_MAX_RECORDS + 1
    assert "no conclusion" in result["interpretation_limit"]



def test_generic_care_continuity_writes_reject_foreign_or_missing_evidence_ids(review_records):
    client_a, _client_b = review_records
    foreign = client_a.post(
        "/api/v1/care-continuity/action-outcomes",
        json={
            "action_id": "appointment-action-a",
            "action_type": "appointment",
            "outcome_status": "reported",
            "evidence_ids": ["review-evidence-b"],
        },
    )
    assert foreign.status_code == 422, foreign.text
    assert "All evidence IDs must belong" in foreign.text

    missing = client_a.post(
        "/api/v1/care-continuity/action-outcomes",
        json={
            "action_id": "appointment-action-a",
            "action_type": "appointment",
            "outcome_status": "reported",
            "evidence_ids": ["evidence-that-does-not-exist"],
        },
    )
    assert missing.status_code == 422, missing.text

    valid = client_a.post(
        "/api/v1/care-continuity/action-outcomes",
        json={
            "action_id": "appointment-action-a",
            "action_type": "appointment",
            "outcome_status": "reported",
            "evidence_ids": ["review-evidence-a"],
        },
    )
    assert valid.status_code == 201, valid.text
    assert valid.json()["evidence_ids"] == ["review-evidence-a"]


def test_evidence_ingestion_key_is_idempotent_and_payload_bound(review_records):
    client_a, client_b = review_records
    payload = {
        "person_id": "review-person-a",
        "type": "caregiver_report",
        "source_text": "Caregiver reports that the afternoon visit happened.",
        "uploaded_by_caregiver_id": "review-caregiver-a",
        "ingestion_key": "capture-retry-20261029-01",
    }
    first = client_a.post("/api/v1/evidence", json=payload)
    assert first.status_code == 200, first.text
    replay = client_a.post("/api/v1/evidence", json=payload)
    assert replay.status_code == 200, replay.text
    assert replay.json()["id"] == first.json()["id"]

    changed = {**payload, "source_text": "Changed payload using the same key."}
    conflict = client_a.post("/api/v1/evidence", json=changed)
    assert conflict.status_code == 409, conflict.text

    # The same opaque key is isolated by care-record ownership.
    other_record = {
        **payload,
        "person_id": "review-person-b",
        "uploaded_by_caregiver_id": "review-caregiver-b",
        "source_text": "A separate person's independent report.",
    }
    separate = client_b.post("/api/v1/evidence", json=other_record)
    assert separate.status_code == 200, separate.text
    assert separate.json()["id"] != first.json()["id"]


def test_evidence_acquisition_provenance_persists_and_is_scoped(review_records):
    client_a, _client_b = review_records
    payload = {
        "person_id": "review-person-a",
        "type": "imported_record",
        "source_text": "Imported care summary; source event date was not provided.",
        "uploaded_by_caregiver_id": "review-caregiver-a",
        "original_author": "Clinic document author",
        "reported_by_label": "Family member who supplied the file",
        "reported_by_caregiver_id": "review-caregiver-a",
        "collected_by_caregiver_id": "review-caregiver-a",
        "reported_at": "2026-10-28T09:15:00+01:00",
        "collection_method": "manual_upload",
        "origin_channel": "web_upload",
        "origin_device_ref": "caregiver-device-label-1",
        "transfer_source": "family_email",
        "transfer_reference": "message-reference-2026-10-28",
    }
    created = client_a.post("/api/v1/evidence", json=payload)
    assert created.status_code == 200, created.text
    saved = created.json()
    assert saved["reported_at"] is not None
    assert saved["collection_method"] == "manual_upload"
    assert saved["origin_device_ref"] == "caregiver-device-label-1"
    assert saved["transfer_source"] == "family_email"
    assert saved["transfer_reference"] == "message-reference-2026-10-28"
    assert saved["uploaded_by_caregiver_id"] == "review-caregiver-a"
    assert saved["original_author"] == "Clinic document author"
    assert saved["source_text_sha256"] == hashlib.sha256(
        payload["source_text"].encode("utf-8")
    ).hexdigest()

