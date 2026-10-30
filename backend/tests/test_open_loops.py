from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.main as main_module
from app.core.database import Base, get_db, SessionLocal as DefaultSessionLocal
from app.main import app
from app.models.care import Evidence

pytestmark = pytest.mark.session_security

engine = create_engine(
    "sqlite:///:memory:",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
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


def session(client, name):
    response = client.post(
        "/api/v1/sessions/anonymous",
        json={"name": name, "caregiver_name": "Caregiver"},
    )
    assert response.status_code == 201, response.text
    body = response.json()
    return body, {"Authorization": f"Bearer {body['access_token']}"}


def create_source_evidence(loop, text="Persisted source record for outcome verification"):
    db = TestSessionLocal()
    try:
        evidence = Evidence(
            person_id=loop["person_id"],
            type="documented_record",
            source_text=text,
            uploaded_by_caregiver_id=loop["caregiver_id"],
        )
        db.add(evidence)
        db.commit()
        db.refresh(evidence)
        return evidence.id
    finally:
        db.close()


def create_independent_corroboration(client, headers, source_id, related_id):
    response = client.post(
        f"/api/v1/care-continuity/evidence/{source_id}/relationships",
        headers=headers,
        json={
            "related_evidence_id": related_id,
            "relationship_type": "corroborates",
            "source_independence_status": "independent",
            "scope_type": "evidence_pair",
            "rationale": "Caregiver reviewed provenance and determined these are separately originated sources.",
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def create_loop(client, headers, loop_type="unresolved_concern", **overrides):
    payload = {
        "loop_type": loop_type,
        "title": "Follow up on the reported change",
        "description": "Caregiver reported a change; cause remains unknown.",
        "evidence": [{"kind": "caregiver_report", "text": "Caregiver report"}],
        **overrides,
    }
    response = client.post("/api/v1/open-loops", json=payload, headers=headers)
    assert response.status_code == 201, response.text
    return response.json()


def test_open_loop_persists_and_lists_only_within_server_issued_session():
    client = TestClient(app)
    first, first_headers = session(client, "First care recipient")
    second, second_headers = session(client, "Second care recipient")
    created = create_loop(client, first_headers)
    assert created["person_id"] == first["person"]["id"]
    assert created["caregiver_id"] == first["caregiver"]["id"]
    assert created["status"] == "open"

    listed = client.get("/api/v1/open-loops", headers=first_headers)
    assert listed.status_code == 200, listed.text
    assert [item["id"] for item in listed.json()["items"]] == [created["id"]]

    isolated = client.get("/api/v1/open-loops", headers=second_headers)
    assert isolated.status_code == 200, isolated.text
    assert isolated.json()["items"] == []
    denied = client.get(f"/api/v1/open-loops/{created['id']}", headers=second_headers)
    assert denied.status_code == 404, denied.text


def test_deadline_requires_timezone_and_source():
    client = TestClient(app)
    _, headers = session(client, "Care recipient")
    no_timezone = create_payload = {
        "loop_type": "follow_up",
        "title": "Follow up",
        "description": "A recorded follow-up",
        "deadline_at": "2026-11-01T09:00:00",
        "deadline_source": "explicit_user",
    }
    response = client.post("/api/v1/open-loops", json=no_timezone, headers=headers)
    assert response.status_code == 422, response.text

    no_source = {
        "loop_type": "follow_up",
        "title": "Follow up",
        "description": "A recorded follow-up",
        "deadline_at": "2026-11-01T09:00:00Z",
    }
    response = client.post("/api/v1/open-loops", json=no_source, headers=headers)
    assert response.status_code == 422, response.text


def test_action_loop_cannot_close_until_outcome_is_verified():
    client = TestClient(app)
    _, headers = session(client, "Care recipient")
    loop = create_loop(client, headers, "pending_action")
    closure = client.post(
        f"/api/v1/open-loops/{loop['id']}/close",
        headers=headers,
        json={"text": "Action was marked complete", "evidence": [{"kind": "action_note"}]},
    )
    assert closure.status_code == 409, closure.text

    reported = client.post(
        f"/api/v1/open-loops/{loop['id']}/outcomes",
        headers=headers,
        json={"text": "Caregiver reports intended outcome", "evidence": [{"kind": "caregiver_report"}]},
    )
    assert reported.status_code == 200, reported.text
    assert reported.json()["outcome_status"] == "outcome_reported"

    ungrounded = client.post(
        f"/api/v1/open-loops/{loop['id']}/verify-outcome",
        headers=headers,
        json={
            "text": "Outcome checked against the stated verification rule",
            "evidence": [{"kind": "supporting_record", "reference": "recorded-by-caregiver"}],
            "verification_rule": "The recorded intended outcome is corroborated by the linked source record.",
        },
    )
    assert ungrounded.status_code == 422, ungrounded.text

    _, other_headers = session(client, "Different care recipient")
    other_loop = create_loop(client, other_headers, "pending_action")
    other_source_id = create_source_evidence(other_loop, "Evidence from another care record")
    cross_person = client.post(
        f"/api/v1/open-loops/{loop['id']}/verify-outcome",
        headers=headers,
        json={
            "text": "Attempt to verify using another person's source",
            "evidence": [{"evidence_id": other_source_id, "kind": "supporting_record"}],
            "verification_rule": "The outcome must be supported by this care record.",
        },
    )
    assert cross_person.status_code == 404, cross_person.text

    source_id = create_source_evidence(loop, "Persisted first source for outcome corroboration")
    second_source_id = create_source_evidence(loop, "Persisted second source with separately reviewed provenance")
    attested = client.post(
        f"/api/v1/open-loops/{loop['id']}/verify-outcome",
        headers=headers,
        json={
            "text": "Caregiver reviewed one linked source",
            "evidence": [{"evidence_id": source_id, "kind": "supporting_record"}],
            "verification_rule": "The intended outcome must be supported by independent source records.",
        },
    )
    assert attested.status_code == 200, attested.text
    assert attested.json()["outcome_status"] == "outcome_reported"
    attestation = attested.json()["outcome_evidence"][-1]
    assert attestation["verification_basis"] == "caregiver_attestation_with_persisted_source_link"
    assert attestation["semantic_grounding_status"] == "not_automatically_verified"

    premature_close = client.post(
        f"/api/v1/open-loops/{loop['id']}/close",
        headers=headers,
        json={"text": "Closure reviewed", "evidence": [{"kind": "closure_review"}]},
    )
    assert premature_close.status_code == 409, premature_close.text

    no_relationship = client.post(
        f"/api/v1/open-loops/{loop['id']}/corroborate-outcome",
        headers=headers,
        json={
            "text": "Attempt corroboration without a lineage assessment",
            "evidence": [{"evidence_id": source_id}, {"evidence_id": second_source_id}],
        },
    )
    assert no_relationship.status_code == 409, no_relationship.text

    relationship = create_independent_corroboration(client, headers, source_id, second_source_id)
    corroborated = client.post(
        f"/api/v1/open-loops/{loop['id']}/corroborate-outcome",
        headers=headers,
        json={
            "text": "Caregiver reviewed the two separately originated source records",
            "evidence": [{"evidence_id": source_id}, {"evidence_id": second_source_id}],
        },
    )
    assert corroborated.status_code == 200, corroborated.text
    assert corroborated.json()["outcome_status"] == "outcome_verified"
    assessment = corroborated.json()["outcome_evidence"][-1]
    assert assessment["source_relationship_id"] == relationship["id"]
    assert assessment["semantic_grounding_status"] == "not_automatically_verified"
    assert "does not automatically establish" in assessment["interpretation_limit"]

    closure = client.post(
        f"/api/v1/open-loops/{loop['id']}/close",
        headers=headers,
        json={"text": "Closure reviewed", "evidence": [{"kind": "closure_review"}]},
    )
    assert closure.status_code == 200, closure.text
    assert closure.json()["verification"] == "verified"
    assert closure.json()["loop"]["status"] == "closed"


def test_open_loop_create_and_update_reject_cross_person_source_ids():
    client = TestClient(app)
    _, headers_a = session(client, "Evidence scope A")
    _, headers_b = session(client, "Evidence scope B")
    other_loop = create_loop(client, headers_b, "pending_action")
    foreign_source_id = create_source_evidence(other_loop, "Source belongs to another care record")

    created = client.post(
        "/api/v1/open-loops",
        headers=headers_a,
        json={
            "loop_type": "pending_decision",
            "title": "Decision with a foreign source",
            "description": "The source must belong to this care record.",
            "evidence": [{"evidence_id": foreign_source_id, "kind": "source_record"}],
        },
    )
    assert created.status_code == 404, created.text

    own_loop = create_loop(client, headers_a, "pending_decision")
    updated = client.patch(
        f"/api/v1/open-loops/{own_loop['id']}",
        headers=headers_a,
        json={"evidence": [{"evidence_id": foreign_source_id, "kind": "source_record"}]},
    )
    assert updated.status_code == 404, updated.text

    closed = client.post(
        f"/api/v1/open-loops/{own_loop['id']}/close",
        headers=headers_a,
        json={"text": "Attempt closure with a foreign source", "evidence": [{"evidence_id": foreign_source_id}]},
    )
    assert closed.status_code == 404, closed.text


def test_professional_input_requires_source_evidence():
    client = TestClient(app)
    _, headers = session(client, "Care recipient")
    loop = create_loop(client, headers, "professional_input")
    response = client.post(
        f"/api/v1/open-loops/{loop['id']}/responses",
        headers=headers,
        json={"text": "A professional responded", "evidence": []},
    )
    assert response.status_code == 422, response.text


def test_professional_input_requires_existing_person_scoped_source_evidence():
    client = TestClient(app)
    person_a, headers_a = session(client, "Care recipient A")
    person_b, headers_b = session(client, "Care recipient B")
    loop_a = create_loop(client, headers_a, "professional_input", title="Confirm source-backed professional input")
    db = TestSessionLocal()
    try:
        db.add(Evidence(
            id="professional-source-evidence-a",
            person_id=person_a["person"]["id"],
            type="document",
            source_text="Synthetic CI document source; not real care information.",
            uploaded_by_caregiver_id=person_a["caregiver"]["id"],
        ))
        db.commit()
    finally:
        db.close()

    caregiver_note_only = client.post(
        f"/api/v1/open-loops/{loop_a['id']}/responses",
        headers=headers_a,
        json={"text": "Caregiver says a professional replied",
              "evidence": [{"kind": "caregiver_recorded", "text": "A professional called"}]},
    )
    assert caregiver_note_only.status_code == 422, caregiver_note_only.text

    foreign_source = client.post(
        f"/api/v1/open-loops/{loop_a['id']}/responses",
        headers=headers_a,
        json={"text": "A professional replied",
              "evidence": [{"kind": "care_record_source", "evidence_id": "evidence-from-another-person"}]},
    )
    assert foreign_source.status_code == 404, foreign_source.text

    accepted = client.post(
        f"/api/v1/open-loops/{loop_a['id']}/responses",
        headers=headers_a,
        json={"text": "The professional response is linked to an existing record",
              "evidence": [{"kind": "care_record_source", "evidence_id": "professional-source-evidence-a"}]},
    )
    assert accepted.status_code == 200, accepted.text
    closed = client.post(
        f"/api/v1/open-loops/{loop_a['id']}/close",
        headers=headers_a,
        json={"text": "Source-backed response reviewed",
              "evidence": [{"kind": "closure_review", "text": "Reviewed against source record"}]},
    )
    assert closed.status_code == 200, closed.text

    loop_b = create_loop(client, headers_b, "professional_input", title="Foreign source must not be visible")
    denied = client.post(
        f"/api/v1/open-loops/{loop_b['id']}/responses",
        headers=headers_b,
        json={"text": "Attempt to reference another care record",
              "evidence": [{"kind": "care_record_source", "evidence_id": "professional-source-evidence-a"}]},
    )
    assert denied.status_code == 404, denied.text


def test_dependency_cycle_is_rejected_and_prerequisite_blocks_dependent():
    client = TestClient(app)
    _, headers = session(client, "Care recipient")
    prerequisite = create_loop(client, headers, "pending_action", title="Prerequisite")
    dependent = create_loop(client, headers, "pending_decision", title="Dependent")

    edge = client.post(
        f"/api/v1/open-loops/{dependent['id']}/dependencies",
        headers=headers,
        json={"prerequisite_id": prerequisite["id"], "mandatory": True},
    )
    assert edge.status_code == 200, edge.text
    assert edge.json()["blocked"] is True

    cycle = client.post(
        f"/api/v1/open-loops/{prerequisite['id']}/dependencies",
        headers=headers,
        json={"prerequisite_id": dependent["id"], "mandatory": True},
    )
    assert cycle.status_code == 409, cycle.text


def test_invalid_loop_type_is_rejected():
    client = TestClient(app)
    _, headers = session(client, "Care recipient")
    response = client.post(
        "/api/v1/open-loops",
        headers=headers,
        json={"loop_type": "invented", "title": "No", "description": "Invalid type"},
    )
    assert response.status_code == 422, response.text


def test_recurring_identical_issue_preserves_separate_occurrences():
    client = TestClient(app)
    _, headers = session(client, "Care recipient")
    first = create_loop(client, headers, "unresolved_concern")
    second = client.post(
        "/api/v1/open-loops",
        headers=headers,
        json={
            "loop_type": "unresolved_concern",
            "title": "Follow up on the reported change",
            "description": "Caregiver reported a change; cause remains unknown.",
            "evidence": [{"kind": "caregiver_report", "text": "Second report"}],
        },
    )
    assert second.status_code == 201, second.text
    body = second.json()
    assert body["id"] == first["id"]
    assert body["recurrence_detected"] is True
    occurrences = client.get(
        f"/api/v1/open-loops/{first['id']}/occurrences", headers=headers
    )
    assert occurrences.status_code == 200, occurrences.text
    assert len(occurrences.json()) == 2

def test_reused_idempotency_key_cannot_commit_a_second_mutation():
    client = TestClient(app)
    _, headers = session(client, "Idempotency recipient")
    payload = {
        "loop_type": "pending_decision",
        "title": "Confirm the appointment plan",
        "description": "The appointment plan still needs confirmation.",
        "idempotency_key": "same-request-key",
    }
    first = client.post("/api/v1/open-loops", headers=headers, json=payload)
    assert first.status_code == 201, first.text

    retry = client.post("/api/v1/open-loops", headers=headers, json=payload)
    assert retry.status_code == 201, retry.text
    assert retry.json()["id"] == first.json()["id"]

    listed = client.get("/api/v1/open-loops", headers=headers)
    assert listed.status_code == 200, listed.text
    assert len(listed.json()["items"]) == 1

def test_overdue_filter_is_applied_before_pagination_and_reports_total():
    client = TestClient(app)
    _, headers = session(client, "Deadline recipient")
    now = datetime.now(timezone.utc)
    create_loop(
        client, headers, "follow_up", title="Future follow-up",
        deadline_at=(now + timedelta(days=2)).isoformat(),
        deadline_source="explicit_user",
    )
    overdue_loop = create_loop(
        client, headers, "follow_up", title="Past follow-up",
        deadline_at=(now - timedelta(days=2)).isoformat(),
        deadline_source="explicit_user",
    )

    response = client.get(
        "/api/v1/open-loops?overdue=true&limit=1", headers=headers
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["count"] == 1
    assert [item["id"] for item in body["items"]] == [overdue_loop["id"]]
    assert body["items"][0]["overdue"] is True

def test_dependent_unblocks_only_after_verified_prerequisite_closure():
    client = TestClient(app)
    _, headers = session(client, "Dependency recipient")
    prerequisite = create_loop(client, headers, "pending_action", title="Complete prerequisite")
    source_id = create_source_evidence(prerequisite, "First persisted source for prerequisite outcome")
    second_source_id = create_source_evidence(prerequisite, "Second separately originated source for prerequisite outcome")
    dependent = create_loop(client, headers, "pending_decision", title="Continue dependent work")

    edge = client.post(
        f"/api/v1/open-loops/{dependent['id']}/dependencies",
        headers=headers,
        json={"prerequisite_id": prerequisite["id"], "mandatory": True},
    )
    assert edge.status_code == 200, edge.text
    assert edge.json()["blocked"] is True

    reported = client.post(
        f"/api/v1/open-loops/{prerequisite['id']}/outcomes",
        headers=headers,
        json={"text": "The prerequisite outcome was reported", "evidence": [{"kind": "caregiver_report"}]},
    )
    assert reported.status_code == 200, reported.text
    attested = client.post(
        f"/api/v1/open-loops/{prerequisite['id']}/verify-outcome",
        headers=headers,
        json={
            "text": "The prerequisite outcome was reviewed by the caregiver",
            "evidence": [{"evidence_id": source_id, "kind": "supporting_record"}],
            "verification_rule": "The recorded outcome is supported by independent source records.",
        },
    )
    assert attested.status_code == 200, attested.text
    assert attested.json()["outcome_status"] == "outcome_reported"
    create_independent_corroboration(client, headers, source_id, second_source_id)
    verified = client.post(
        f"/api/v1/open-loops/{prerequisite['id']}/corroborate-outcome",
        headers=headers,
        json={
            "text": "Two separately originated source records were reviewed",
            "evidence": [{"evidence_id": source_id}, {"evidence_id": second_source_id}],
        },
    )
    assert verified.status_code == 200, verified.text
    closed = client.post(
        f"/api/v1/open-loops/{prerequisite['id']}/close",
        headers=headers,
        json={"text": "Prerequisite closure reviewed", "evidence": [{"kind": "closure_review"}]},
    )
    assert closed.status_code == 200, closed.text

    refreshed = client.get(f"/api/v1/open-loops/{dependent['id']}", headers=headers)
    assert refreshed.status_code == 200, refreshed.text
    assert refreshed.json()["loop"]["status"] == "open"
    history = refreshed.json()["history"]
    assert any(item["event_type"] == "dependency_unblocked" for item in history)

def test_contradiction_closure_uses_persisted_reconciliation_not_magic_words():
    client = TestClient(app)
    _, headers = session(client, "Contradiction recipient")
    loop = create_loop(client, headers, "contradiction", title="Two conflicting reports")
    source_id = create_source_evidence(loop, "Persisted source comparison for contradiction")

    premature = client.post(
        f"/api/v1/open-loops/{loop['id']}/close",
        headers=headers,
        json={"text": "Reviewed", "evidence": [{"kind": "closure_review"}]},
    )
    assert premature.status_code == 409, premature.text

    ungrounded_reconciliation = client.post(
        f"/api/v1/open-loops/{loop['id']}/reconcile",
        headers=headers,
        json={
            "text": "The two reports refer to different dates; neither is discarded.",
            "evidence": [{"kind": "source_comparison"}],
        },
    )
    assert ungrounded_reconciliation.status_code == 422, ungrounded_reconciliation.text

    reconciled = client.post(
        f"/api/v1/open-loops/{loop['id']}/reconcile",
        headers=headers,
        json={
            "text": "The two reports refer to different dates; neither is discarded.",
            "evidence": [{"evidence_id": source_id, "kind": "source_comparison"}],
        },
    )
    assert reconciled.status_code == 200, reconciled.text
    reconciliation_record = reconciled.json()["outcome_evidence"][-1]
    assert reconciliation_record["semantic_grounding_status"] == "not_automatically_verified"
    assert "do not automatically establish semantic entailment" in reconciliation_record["interpretation_limit"]

    assert reconciled.json()["outcome_status"] == "outcome_reported"
    second_source_id = create_source_evidence(loop, "Independent second source for contradiction review")
    blocked_close = client.post(
        f"/api/v1/open-loops/{loop['id']}/close",
        headers=headers,
        json={"text": "Reconciliation alone is not corroboration", "evidence": [{"kind": "closure_review"}]},
    )
    assert blocked_close.status_code == 409, blocked_close.text
    create_independent_corroboration(client, headers, source_id, second_source_id)
    corroborated = client.post(
        f"/api/v1/open-loops/{loop['id']}/corroborate-outcome",
        headers=headers,
        json={
            "text": "Independent source lineage reviewed for the reconciled claim",
            "evidence": [{"evidence_id": source_id}, {"evidence_id": second_source_id}],
            "verification_rule": "The reconciled outcome must be supported by independently originated records.",
        },
    )
    assert corroborated.status_code == 200, corroborated.text
    closed = client.post(
        f"/api/v1/open-loops/{loop['id']}/close",
        headers=headers,
        json={"text": "The record was reviewed against the corroborated reconciliation.", "evidence": [{"kind": "closure_review"}]},
    )
    assert closed.status_code == 200, closed.text
    assert closed.json()["loop"]["status"] == "closed"

