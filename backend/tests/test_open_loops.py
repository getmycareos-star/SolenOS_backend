from datetime import datetime, timedelta, timezone

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.main as main_module
import app.api.open_loops as open_loop_api
from app.api.care_continuity import _build_situation_understanding_assessment
from app.core.database import Base, get_db, SessionLocal as DefaultSessionLocal
from app.main import app
from app.models.care import Evidence
from app.models.change import Change
from app.core.change_enums import ChangeType

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

def current_loop_version(client, headers, loop_id):
    response = client.get(f"/api/v1/open-loops/{loop_id}", headers=headers)
    assert response.status_code == 200, response.text
    return response.json()["loop"]["version"]


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



def test_concurrent_create_audit_collision_returns_the_committed_occurrence(monkeypatch):
    client = TestClient(app)
    _, headers = session(client, "Concurrent idempotency recipient")
    payload = {
        "loop_type": "awaiting_response",
        "title": "Confirm the clinic callback",
        "description": "The clinic callback is not yet confirmed.",
        "request_text": "Please confirm the callback time.",
        "recipient_label": "CI-only clinic",
        "evidence": [{"kind": "ci_fixture", "text": "Concurrent create race regression."}],
        "idempotency_key": "concurrent-create-audit-collision",
    }
    original_audit = open_loop_api._audit

    def simulate_winning_request(
        db, loop, event_type, before, evidence=None, key=None,
        actor="session_caregiver", event_context=None,
    ):
        if event_type == "created" and key == payload["idempotency_key"]:
            # Model the interleaving where another request commits the same
            # occurrence after this request's initial lookup but before audit.
            db.rollback()
            db.add(loop)
            db.flush()
            db.add(open_loop_api.OpenLoopOccurrence(
                loop_id=loop.id,
                person_id=loop.person_id,
                caregiver_id=loop.caregiver_id,
                source_type=loop.source_type,
                source_id=loop.source_id,
                source_fingerprint=loop.source_fingerprint,
                evidence=loop.evidence or [],
                idempotency_key=key,
            ))
            db.add(open_loop_api.OpenLoopEvent(
                loop_id=loop.id,
                person_id=loop.person_id,
                caregiver_id=loop.caregiver_id,
                event_type="created",
                before_state=None,
                after_state={
                    **open_loop_api._snapshot(loop),
                    **({"event_context": event_context} if event_context else {}),
                },
                evidence=evidence or [],
                idempotency_key=key,
                actor=actor,
            ))
            db.commit()
            raise HTTPException(
                status_code=409,
                detail="Idempotency key has already been used for this care record",
            )
        return original_audit(
            db, loop, event_type, before, evidence, key, actor,
            event_context=event_context,
        )

    monkeypatch.setattr(open_loop_api, "_audit", simulate_winning_request)
    response = client.post("/api/v1/open-loops", headers=headers, json=payload)
    assert response.status_code == 201, response.text
    created = response.json()
    assert created["recurrence_detected"] is True
    assert created["id"]
    listed = client.get("/api/v1/open-loops", headers=headers)
    assert listed.status_code == 200, listed.text
    assert [item["id"] for item in listed.json()["items"]] == [created["id"]]


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

def test_system_reassessment_audits_deadlines_without_infering_completion():
    client = TestClient(app)
    _, headers = session(client, "System follow-up recipient")
    now = datetime.now(timezone.utc)
    overdue = create_loop(
        client, headers, "follow_up", title="Check the outstanding clinic result",
        deadline_at=(now - timedelta(days=1)).isoformat(),
        deadline_source="explicit_user",
    )
    due_soon = create_loop(
        client, headers, "follow_up", title="Prepare the upcoming appointment handoff",
        deadline_at=(now + timedelta(hours=12)).isoformat(),
        deadline_source="explicit_user",
    )

    reassessed = client.post("/api/v1/open-loops/reassess", headers=headers)
    assert reassessed.status_code == 200, reassessed.text
    assert reassessed.json()["audited_count"] >= 2

    due_soon_current = client.get(f"/api/v1/open-loops/{due_soon['id']}", headers=headers)
    assert due_soon_current.status_code == 200, due_soon_current.text
    assert due_soon_current.json()["loop"]["priority"] == "high"
    assert "within 24 hours" in " ".join(due_soon_current.json()["loop"]["priority_reasons"])
    due_soon_history = client.get(
        f"/api/v1/open-loops/{due_soon['id']}/history", headers=headers
    )
    assert due_soon_history.status_code == 200, due_soon_history.text
    assert any(
        item.get("deadline_within_24_hours") is True
        and item.get("completion_inferred") is False
        for event in due_soon_history.json()
        if event["event_type"] == "reassessed"
        for item in (event.get("evidence") or [])
    )

    current = client.get(f"/api/v1/open-loops/{overdue['id']}", headers=headers)
    assert current.status_code == 200, current.text
    assert current.json()["loop"]["status"] == "open"
    assert current.json()["loop"]["priority"] == "high"
    assert current.json()["loop"]["closure_result"] is None

    history = client.get(f"/api/v1/open-loops/{overdue['id']}/history", headers=headers)
    assert history.status_code == 200, history.text
    reassessment_events = [
        event for event in history.json()
        if event["event_type"] == "reassessed"
    ]
    assert reassessment_events
    assert any(
        item.get("deadline_passed") is True
        and item.get("completion_inferred") is False
        for event in reassessment_events
        for item in (event.get("evidence") or [])
    )


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



def test_situation_assessment_preserves_saved_deferral_and_review_context_per_person():
    client = TestClient(app)
    first, first_headers = session(client, "Deferred assessment recipient")
    second, second_headers = session(client, "Separate assessment recipient")
    review_at = "2026-11-05T10:00:00+00:00"
    deferred = create_loop(
        client,
        first_headers,
        "follow_up",
        title="Wait for the recorded appointment",
        deferred_reason="The family recorded that this follow-up is deferred until the appointment.",
        review_at=review_at,
        request_text="Ask the clinic to confirm the appointment time.",
        verification_rule="The appointment time is confirmed in a saved clinic message.",
    )

    with TestSessionLocal() as db:
        first_assessment = _build_situation_understanding_assessment(db, first["person"]["id"])
        second_assessment = _build_situation_understanding_assessment(db, second["person"]["id"])

    saved = next(
        item for item in first_assessment["open_loops"]["items"]
        if item["id"] == deferred["id"] and item["source"] == "open_loop_records"
    )
    assert saved["deferred_reason"] == "The family recorded that this follow-up is deferred until the appointment."
    assert saved["review_at"].startswith("2026-11-05T10:00:00")
    assert saved["request_text"] == "Ask the clinic to confirm the appointment time."
    assert saved["verification_rule"] == "The appointment time is confirmed in a saved clinic message."
    assert saved["priority"] == deferred["priority"]
    assert saved["priority_reasons"] == deferred["priority_reasons"]
    assert all(item["id"] != deferred["id"] for item in second_assessment["open_loops"]["items"])


def test_deferred_follow_up_reopens_from_persisted_review_time_after_restart():
    client = TestClient(app)
    _, headers = session(client, "Deferred recovery recipient")
    loop = create_loop(
        client,
        headers,
        loop_type="follow_up",
        deadline_at=(datetime.now(timezone.utc) + timedelta(days=2)).isoformat(),
        deadline_source="explicit_user",
    )
    review_at = datetime.now(timezone.utc) + timedelta(days=4)
    deferred = client.patch(
        f"/api/v1/open-loops/{loop['id']}",
        headers=headers,
        json={
            "status": "deferred",
            "expected_version": loop["version"],
            "deferred_reason": "Waiting for the clinic to return the call.",
            "review_at": review_at.isoformat(),
        },
    )
    assert deferred.status_code == 200, deferred.text
    assert deferred.json()["status"] == "deferred"

    db = TestSessionLocal()
    try:
        # Simulate a process being down until after the persisted deadline.
        recovered = open_loop_api._reopen_due_deferred_loops(
            db, datetime.now(timezone.utc) + timedelta(days=3)
        )
        db.commit()
        row = db.query(open_loop_api.OpenLoopRecord).filter_by(id=loop["id"]).one()
        assert recovered == 1
        assert row.status == "open"
        assert row.review_at is None
        assert row.deferred_reason == "Waiting for the clinic to return the call."
        assert row.priority == "high"
        assert "Persisted deadline has passed" in row.priority_reasons
        event = db.query(open_loop_api.OpenLoopEvent).filter_by(
            loop_id=loop["id"], event_type="deferred_review_due"
        ).one()
        assert event.actor == "solenos_reconciliation"
        assert event.before_state["status"] == "deferred"
        assert event.after_state["status"] == "open"
        assert event.evidence[0]["completion_inferred"] is False
        assert event.evidence[0]["deadline_due"] is True
    finally:
        db.close()


def test_follow_up_callback_promise_remains_waiting_and_unverified():
    client = TestClient(app)
    _, headers = session(client, "Callback follow-up recipient")
    loop = create_loop(client, headers, "follow_up", title="Arrange specialist appointment")

    payload = {
        "text": "Sarah reports the clinic said someone would call her back. No callback has yet been reported.",
        "response_outcome": "callback_promised",
        "evidence": [{"kind": "caregiver_recorded", "text": "Sarah's report; not independent clinic evidence."}],
        "idempotency_key": "clinic-callback-promised-once",
    }
    response = client.post(
        f"/api/v1/open-loops/{loop['id']}/responses",
        headers=headers,
        json=payload,
    )
    assert response.status_code == 200, response.text
    saved = response.json()
    assert saved["id"] == loop["id"]
    assert saved["status"] == "waiting"
    assert saved["outcome_status"] == "outcome_unknown"
    assert len(saved["response_evidence"]) == 1
    event = saved["response_evidence"][0]
    assert event["response_outcome"] == "callback_promised"
    assert event["reported_by"] == "session_caregiver"
    assert event["independently_verified"] is False

    replay = client.post(
        f"/api/v1/open-loops/{loop['id']}/responses",
        headers=headers,
        json=payload,
    )
    assert replay.status_code == 200, replay.text
    assert len(replay.json()["response_evidence"]) == 1


def test_open_filter_includes_waiting_callback_promises_as_unresolved():
    client = TestClient(app)
    _, headers = session(client, "Open unresolved follow-up filter recipient")
    ordinary = create_loop(
        client, headers, "follow_up",
        title="Confirm specialist appointment",
    )
    waiting = create_loop(
        client, headers, "follow_up",
        title="Wait for clinic callback",
    )
    callback = client.post(
        f"/api/v1/open-loops/{waiting['id']}/responses",
        headers=headers,
        json={
            "text": "Caregiver reports that the clinic promised a callback; no appointment is confirmed.",
            "response_outcome": "callback_promised",
            "idempotency_key": "open-filter-callback-promise",
        },
    )
    assert callback.status_code == 200, callback.text
    assert callback.json()["status"] == "waiting"

    unresolved = client.get("/api/v1/open-loops?status=open&limit=100", headers=headers)
    assert unresolved.status_code == 200, unresolved.text
    unresolved_ids = {item["id"] for item in unresolved.json()["items"]}
    assert ordinary["id"] in unresolved_ids
    assert waiting["id"] in unresolved_ids, "a promised callback remains an unresolved commitment"

    waiting_only = client.get("/api/v1/open-loops?status=waiting&limit=100", headers=headers)
    assert waiting_only.status_code == 200, waiting_only.text
    assert {item["id"] for item in waiting_only.json()["items"]} == {waiting["id"]}


def test_follow_up_rejects_unknown_response_outcome():
    client = TestClient(app)
    _, headers = session(client, "Invalid callback outcome recipient")
    loop = create_loop(client, headers, "follow_up")
    response = client.post(
        f"/api/v1/open-loops/{loop['id']}/responses",
        headers=headers,
        json={"text": "A clinic response was reported.", "response_outcome": "appointment_booked"},
    )
    assert response.status_code == 422, response.text

def test_open_loop_offset_pagination_returns_stable_complete_pages():
    client = TestClient(app)
    _, headers = session(client, "Paginated follow-up recipient")
    created = [
        create_loop(
            client,
            headers,
            loop_type="follow_up",
            title=f"Follow-up page {index}",
            description=f"Persisted follow-up record {index}",
        )
        for index in range(3)
    ]

    first = client.get("/api/v1/open-loops?limit=2&offset=0", headers=headers)
    # A mutable deadline/status/update timestamp must not move an existing
    # row across the page boundary while the caregiver loads the next page.
    changed_between_pages = client.patch(
        f"/api/v1/open-loops/{created[0]['id']}",
        headers=headers,
        json={
            "description": "Edited while the next page is loading",
            "idempotency_key": "pagination-update-between-pages",
        },
    )
    assert changed_between_pages.status_code == 200, changed_between_pages.text
    second = client.get("/api/v1/open-loops?limit=2&offset=2", headers=headers)
    repeated_first = client.get("/api/v1/open-loops?limit=2&offset=0", headers=headers)

    assert first.status_code == 200, first.text
    assert second.status_code == 200, second.text
    assert repeated_first.status_code == 200, repeated_first.text
    first_body = first.json()
    second_body = second.json()
    assert first_body["count"] == 3
    assert second_body["count"] == 3
    assert len(first_body["items"]) == 2
    assert len(second_body["items"]) == 1
    first_ids = [item["id"] for item in first_body["items"]]
    second_ids = [item["id"] for item in second_body["items"]]
    assert first_ids == [item["id"] for item in repeated_first.json()["items"]]
    assert len(set(first_ids + second_ids)) == 3
    assert set(first_ids + second_ids) == {item["id"] for item in created}

def test_open_loop_event_and_occurrence_history_support_offset_pagination():
    client = TestClient(app)
    _, headers = session(client, "Paginated follow-up history recipient")
    loop = create_loop(client, headers, loop_type="follow_up")

    for index in range(2):
        updated = client.patch(
            f"/api/v1/open-loops/{loop['id']}",
            headers=headers,
            json={
                "description": f"Updated follow-up history entry {index}",
                "idempotency_key": f"history-page-update-{index}",
            },
        )
        assert updated.status_code == 200, updated.text

    db = TestSessionLocal()
    try:
        for index in range(3):
            db.add(open_loop_api.OpenLoopOccurrence(
                loop_id=loop["id"],
                person_id=loop["person_id"],
                caregiver_id=loop["caregiver_id"],
                source_type="daily_care_entry",
                source_id=f"history-page-source-{index}",
                source_fingerprint=f"{index + 1:064x}",
                evidence=[],
                idempotency_key=f"history-page-occurrence-{index}",
                occurred_at=datetime(2026, 11, 20, 10 + index, tzinfo=timezone.utc),
            ))
        db.commit()
    finally:
        db.close()

    history_first = client.get(
        f"/api/v1/open-loops/{loop['id']}/history?limit=2&offset=0",
        headers=headers,
    )
    history_second = client.get(
        f"/api/v1/open-loops/{loop['id']}/history?limit=2&offset=2",
        headers=headers,
    )
    occurrence_first = client.get(
        f"/api/v1/open-loops/{loop['id']}/occurrences?limit=2&offset=0",
        headers=headers,
    )
    occurrence_second = client.get(
        f"/api/v1/open-loops/{loop['id']}/occurrences?limit=2&offset=2",
        headers=headers,
    )

    for response in (history_first, history_second, occurrence_first, occurrence_second):
        assert response.status_code == 200, response.text
    history_ids = [row["id"] for row in history_first.json() + history_second.json()]
    occurrence_ids = [row["id"] for row in occurrence_first.json() + occurrence_second.json()]
    assert len(history_ids) == 3
    assert len(set(history_ids)) == 3
    assert len(occurrence_ids) == 4
    assert len(set(occurrence_ids)) == 4



def test_persisted_change_can_be_linked_to_person_scoped_follow_up():
    client = TestClient(app)
    first, first_headers = session(client, "Change follow-up recipient")
    second, second_headers = session(client, "Unrelated care recipient")
    db = TestSessionLocal()
    try:
        change = Change(
            person_id=first["person"]["id"],
            subject_type="sleep",
            subject_id=first["person"]["id"],
            change_type=ChangeType.EMERGENCE,
            evidence_ids=[],
            explanation="Persisted change source for follow-up isolation.",
            attention_candidate=True,
            follow_up_candidate=True,
        )
        db.add(change)
        db.commit()
        db.refresh(change)
        change_id = change.id
    finally:
        db.close()

    created = client.post(
        "/api/v1/open-loops",
        headers=first_headers,
        json={
            "loop_type": "follow_up",
            "title": "Review the saved change",
            "description": "A persisted change needs a caregiver follow-up.",
            "source_type": "persisted_change",
            "source_id": change_id,
            "evidence": [],
            "uncertainty": "unknown",
            "idempotency_key": "persisted-change-follow-up",
        },
    )
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["source_type"] == "persisted_change"
    assert body["source_id"] == change_id

    missing_source = client.post(
        "/api/v1/open-loops",
        headers=first_headers,
        json={
            "loop_type": "follow_up",
            "title": "Missing persisted source",
            "description": "A persisted change follow-up must identify its source.",
            "source_type": "persisted_change",
            "evidence": [],
            "uncertainty": "unknown",
        },
    )
    assert missing_source.status_code == 422, missing_source.text

    foreign_link = client.post(
        "/api/v1/open-loops",
        headers=second_headers,
        json={
            "loop_type": "follow_up",
            "title": "Try to link another person's change",
            "description": "This must not cross care-record boundaries.",
            "source_type": "persisted_change",
            "source_id": change_id,
            "evidence": [],
            "uncertainty": "unknown",
        },
    )
    assert foreign_link.status_code == 404, foreign_link.text

    listed = client.get("/api/v1/open-loops?limit=100", headers=first_headers)
    assert listed.status_code == 200, listed.text
    linked = next(item for item in listed.json()["items"] if item["id"] == body["id"])
    assert linked["source_type"] == "persisted_change"
    assert linked["source_id"] == change_id

    with TestSessionLocal() as db:
        persisted_change = db.query(Change).filter(Change.id == change_id).one()
        persisted_change.is_active = False
        db.commit()
    inactive_link = client.post(
        "/api/v1/open-loops",
        headers=first_headers,
        json={
            "loop_type": "follow_up",
            "title": "Link an inactive change",
            "description": "Inactive change references must not create new follow-ups.",
            "source_type": "persisted_change",
            "source_id": change_id,
            "evidence": [],
            "uncertainty": "unknown",
            "idempotency_key": "inactive-persisted-change-follow-up",
        },
    )
    assert inactive_link.status_code == 404, inactive_link.text

    isolated = client.get("/api/v1/open-loops?limit=100", headers=second_headers)
    assert isolated.status_code == 200, isolated.text
    assert isolated.json()["items"] == []


def test_action_idempotency_key_rejects_changed_payload_and_replays_identical_payload():
    client = TestClient(app)
    _, headers = session(client, "Care recipient")
    loop = create_loop(client, headers, "pending_action")
    key = "action-key-payload-integrity"
    first_payload = {
        "text": "Caregiver recorded a completed call",
        "evidence": [{"kind": "caregiver_report", "text": "Call completed"}],
        "idempotency_key": key,
    }
    first = client.post(f"/api/v1/open-loops/{loop['id']}/actions", headers=headers, json=first_payload)
    retry = client.post(f"/api/v1/open-loops/{loop['id']}/actions", headers=headers, json=first_payload)
    assert first.status_code == 200, first.text
    assert retry.status_code == 200, retry.text
    assert len(retry.json()["action_evidence"]) == 1

    changed_payload = {
        "text": "Different action must not be accepted as a retry",
        "evidence": [{"kind": "caregiver_report", "text": "Different action"}],
        "idempotency_key": key,
    }
    conflict = client.post(f"/api/v1/open-loops/{loop['id']}/actions", headers=headers, json=changed_payload)
    assert conflict.status_code == 409, conflict.text
    current = client.get(f"/api/v1/open-loops/{loop['id']}", headers=headers)
    assert current.status_code == 200, current.text
    actions = current.json()["loop"]["action_evidence"]
    assert len(actions) == 1
    assert actions[0]["text"] == first_payload["text"]



def test_response_outcome_and_update_retries_reject_changed_payloads():
    client = TestClient(app)
    _, headers = session(client, "Care recipient")

    response_loop = create_loop(client, headers, "awaiting_response")
    response_key = "response-key-payload-integrity"
    response_payload = {
        "text": "The clinic confirmed the callback time.",
        "evidence": [{"kind": "caregiver_report", "text": "Clinic called back."}],
        "idempotency_key": response_key,
    }
    first_response = client.post(
        f"/api/v1/open-loops/{response_loop['id']}/responses",
        headers=headers,
        json=response_payload,
    )
    same_response = client.post(
        f"/api/v1/open-loops/{response_loop['id']}/responses",
        headers=headers,
        json=response_payload,
    )
    changed_response = client.post(
        f"/api/v1/open-loops/{response_loop['id']}/responses",
        headers=headers,
        json={**response_payload, "text": "A different callback was reported."},
    )
    assert first_response.status_code == 200, first_response.text
    assert same_response.status_code == 200, same_response.text
    assert len(same_response.json()["response_evidence"]) == 1
    assert changed_response.status_code == 409, changed_response.text

    outcome_loop = create_loop(client, headers, "pending_action")
    outcome_key = "outcome-key-payload-integrity"
    outcome_payload = {
        "text": "Caregiver reported the action was completed.",
        "evidence": [{"kind": "caregiver_report", "text": "The visit took place."}],
        "idempotency_key": outcome_key,
    }
    first_outcome = client.post(
        f"/api/v1/open-loops/{outcome_loop['id']}/outcomes",
        headers=headers,
        json=outcome_payload,
    )
    same_outcome = client.post(
        f"/api/v1/open-loops/{outcome_loop['id']}/outcomes",
        headers=headers,
        json=outcome_payload,
    )
    changed_outcome = client.post(
        f"/api/v1/open-loops/{outcome_loop['id']}/outcomes",
        headers=headers,
        json={**outcome_payload, "evidence": [{"kind": "caregiver_report", "text": "A different event."}]},
    )
    assert first_outcome.status_code == 200, first_outcome.text
    assert same_outcome.status_code == 200, same_outcome.text
    assert len(same_outcome.json()["outcome_evidence"]) == 1
    assert changed_outcome.status_code == 409, changed_outcome.text

    update_loop_record = create_loop(client, headers, "pending_action")
    update_key = "update-key-payload-integrity"
    first_update = client.patch(
        f"/api/v1/open-loops/{update_loop_record['id']}",
        headers=headers,
        json={"title": "Updated title", "idempotency_key": update_key},
    )
    same_update = client.patch(
        f"/api/v1/open-loops/{update_loop_record['id']}",
        headers=headers,
        json={"title": "Updated title", "idempotency_key": update_key},
    )
    changed_update = client.patch(
        f"/api/v1/open-loops/{update_loop_record['id']}",
        headers=headers,
        json={"title": "A different title", "idempotency_key": update_key},
    )
    assert first_update.status_code == 200, first_update.text
    assert same_update.status_code == 200, same_update.text
    assert changed_update.status_code == 409, changed_update.text



def test_closure_retry_rejects_changed_closure_note():
    client = TestClient(app)
    _, headers = session(client, "Care recipient")
    loop = create_loop(client, headers, "awaiting_response")

    response = client.post(
        f"/api/v1/open-loops/{loop['id']}/responses",
        headers=headers,
        json={
            "text": "The clinic returned the call.",
            "evidence": [{"kind": "caregiver_report", "text": "The clinic returned the call."}],
        },
    )
    assert response.status_code == 200, response.text

    close_payload = {
        "text": "Caregiver confirmed the follow-up was completed.",
        "evidence": [{"kind": "caregiver_report", "text": "The follow-up was completed."}],
        "idempotency_key": "closure-note-payload-integrity",
    }
    first = client.post(
        f"/api/v1/open-loops/{loop['id']}/close",
        headers=headers,
        json=close_payload,
    )
    retry = client.post(
        f"/api/v1/open-loops/{loop['id']}/close",
        headers=headers,
        json=close_payload,
    )
    changed = client.post(
        f"/api/v1/open-loops/{loop['id']}/close",
        headers=headers,
        json={**close_payload, "text": "A different closure note."},
    )

    assert first.status_code == 200, first.text
    assert first.json()["accepted"] is True
    assert retry.status_code == 200, retry.text
    assert retry.json()["accepted"] is True
    assert changed.status_code == 409, changed.text


def test_all_remaining_open_loop_retries_reject_changed_mutation_payloads():
    client = TestClient(app)
    _, headers = session(client, "Remaining retry recipient")

    cancelled_loop = create_loop(client, headers, "follow_up")
    cancel_payload = {
        "text": "The family chose not to continue this follow-up.",
        "evidence": [],
        "idempotency_key": "cancel-payload-integrity",
    }
    first_cancel = client.post(
        f"/api/v1/open-loops/{cancelled_loop['id']}/cancel",
        headers=headers,
        json=cancel_payload,
    )
    same_cancel = client.post(
        f"/api/v1/open-loops/{cancelled_loop['id']}/cancel",
        headers=headers,
        json=cancel_payload,
    )
    changed_cancel = client.post(
        f"/api/v1/open-loops/{cancelled_loop['id']}/cancel",
        headers=headers,
        json={**cancel_payload, "text": "A different cancellation reason."},
    )
    assert first_cancel.status_code == 200, first_cancel.text
    assert same_cancel.status_code == 200, same_cancel.text
    assert changed_cancel.status_code == 409, changed_cancel.text

    reopen_payload = {
        "text": "The family wants to reopen this follow-up.",
        "evidence": [],
        "idempotency_key": "reopen-payload-integrity",
    }
    first_reopen = client.post(
        f"/api/v1/open-loops/{cancelled_loop['id']}/reopen",
        headers=headers,
        json=reopen_payload,
    )
    same_reopen = client.post(
        f"/api/v1/open-loops/{cancelled_loop['id']}/reopen",
        headers=headers,
        json=reopen_payload,
    )
    changed_reopen = client.post(
        f"/api/v1/open-loops/{cancelled_loop['id']}/reopen",
        headers=headers,
        json={**reopen_payload, "text": "A different reason for reopening."},
    )
    assert first_reopen.status_code == 200, first_reopen.text
    assert same_reopen.status_code == 200, same_reopen.text
    assert changed_reopen.status_code == 409, changed_reopen.text

    contradiction = create_loop(client, headers, "contradiction")
    contradiction_source = create_source_evidence(contradiction)
    reconciliation_payload = {
        "text": "The reports describe different dates; neither account is discarded.",
        "evidence": [{"evidence_id": contradiction_source, "kind": "source_comparison"}],
        "idempotency_key": "reconciliation-payload-integrity",
    }
    first_reconciliation = client.post(
        f"/api/v1/open-loops/{contradiction['id']}/reconcile",
        headers=headers,
        json=reconciliation_payload,
    )
    same_reconciliation = client.post(
        f"/api/v1/open-loops/{contradiction['id']}/reconcile",
        headers=headers,
        json=reconciliation_payload,
    )
    changed_reconciliation = client.post(
        f"/api/v1/open-loops/{contradiction['id']}/reconcile",
        headers=headers,
        json={**reconciliation_payload, "text": "The reports describe the same date."},
    )
    assert first_reconciliation.status_code == 200, first_reconciliation.text
    assert same_reconciliation.status_code == 200, same_reconciliation.text
    assert changed_reconciliation.status_code == 409, changed_reconciliation.text

    verified_loop = create_loop(client, headers, "pending_action")
    verification_source = create_source_evidence(verified_loop)
    verification_payload = {
        "text": "The saved clinic note supports the reported outcome.",
        "evidence": [{"evidence_id": verification_source}],
        "verification_rule": "The outcome must match the saved clinic note.",
        "idempotency_key": "verification-payload-integrity",
    }
    first_verification = client.post(
        f"/api/v1/open-loops/{verified_loop['id']}/verify-outcome",
        headers=headers,
        json=verification_payload,
    )
    same_verification = client.post(
        f"/api/v1/open-loops/{verified_loop['id']}/verify-outcome",
        headers=headers,
        json=verification_payload,
    )
    changed_verification = client.post(
        f"/api/v1/open-loops/{verified_loop['id']}/verify-outcome",
        headers=headers,
        json={**verification_payload, "text": "A different outcome was verified."},
    )
    assert first_verification.status_code == 200, first_verification.text
    assert same_verification.status_code == 200, same_verification.text
    assert changed_verification.status_code == 409, changed_verification.text

    corroborated_loop = create_loop(client, headers, "pending_action")
    first_source = create_source_evidence(corroborated_loop, "First independent corroboration source.")
    second_source = create_source_evidence(corroborated_loop, "Second independent corroboration source.")
    create_independent_corroboration(client, headers, first_source, second_source)
    corroboration_payload = {
        "text": "Two separately originated records were reviewed.",
        "evidence": [{"evidence_id": first_source}, {"evidence_id": second_source}],
        "verification_rule": "The outcome must be supported by independent records.",
        "idempotency_key": "corroboration-payload-integrity",
    }
    first_corroboration = client.post(
        f"/api/v1/open-loops/{corroborated_loop['id']}/corroborate-outcome",
        headers=headers,
        json=corroboration_payload,
    )
    same_corroboration = client.post(
        f"/api/v1/open-loops/{corroborated_loop['id']}/corroborate-outcome",
        headers=headers,
        json=corroboration_payload,
    )
    changed_corroboration = client.post(
        f"/api/v1/open-loops/{corroborated_loop['id']}/corroborate-outcome",
        headers=headers,
        json={**corroboration_payload, "text": "A different corroboration statement."},
    )
    assert first_corroboration.status_code == 200, first_corroboration.text
    assert same_corroboration.status_code == 200, same_corroboration.text
    assert changed_corroboration.status_code == 409, changed_corroboration.text



def test_outcome_verification_retry_rejects_changed_ignored_rule():
    client = TestClient(app)
    _, headers = session(client, "Verification rule recipient")
    original_rule = "The recorded outcome must match the saved clinic letter."
    loop = create_loop(
        client, headers, "pending_action",
        verification_rule=original_rule,
    )
    source_id = create_source_evidence(loop, "Saved clinic letter supporting the outcome.")
    payload = {
        "text": "The outcome matches the saved clinic letter.",
        "evidence": [{"evidence_id": source_id}],
        "idempotency_key": "verification-rule-payload-integrity",
    }

    first = client.post(
        f"/api/v1/open-loops/{loop['id']}/verify-outcome",
        headers=headers, json=payload,
    )
    identical = client.post(
        f"/api/v1/open-loops/{loop['id']}/verify-outcome",
        headers=headers, json=payload,
    )
    changed_ignored_rule = client.post(
        f"/api/v1/open-loops/{loop['id']}/verify-outcome",
        headers=headers,
        json={**payload, "verification_rule": "A different verification rule."},
    )

    assert first.status_code == 200, first.text
    assert identical.status_code == 200, identical.text
    assert changed_ignored_rule.status_code == 409, changed_ignored_rule.text

    corroborated = create_loop(
        client, headers, "pending_action",
        verification_rule=original_rule,
    )
    first_source = create_source_evidence(corroborated, "First independent clinic record.")
    second_source = create_source_evidence(corroborated, "Second independent clinic record.")
    create_independent_corroboration(client, headers, first_source, second_source)
    corroboration_payload = {
        "text": "Two separately originated clinic records support the reported outcome.",
        "evidence": [
            {"evidence_id": first_source},
            {"evidence_id": second_source},
        ],
        "idempotency_key": "corroboration-rule-payload-integrity",
    }
    first_corroboration = client.post(
        f"/api/v1/open-loops/{corroborated['id']}/corroborate-outcome",
        headers=headers, json=corroboration_payload,
    )
    identical_corroboration = client.post(
        f"/api/v1/open-loops/{corroborated['id']}/corroborate-outcome",
        headers=headers, json=corroboration_payload,
    )
    changed_corroboration_rule = client.post(
        f"/api/v1/open-loops/{corroborated['id']}/corroborate-outcome",
        headers=headers,
        json={**corroboration_payload, "verification_rule": "A different corroboration rule."},
    )
    assert first_corroboration.status_code == 200, first_corroboration.text
    assert identical_corroboration.status_code == 200, identical_corroboration.text
    assert changed_corroboration_rule.status_code == 409, changed_corroboration_rule.text


def test_create_idempotency_rejects_changed_deadline_and_evidence():
    client = TestClient(app)
    _, headers = session(client, "Create payload recipient")
    key = "create-full-payload-integrity"
    first_payload = {
        "loop_type": "follow_up",
        "title": "Confirm the clinic appointment",
        "description": "The appointment date still needs confirmation.",
        "evidence": [{"kind": "caregiver_report", "text": "Clinic asked us to call back."}],
        "deadline_at": (datetime.now(timezone.utc) + timedelta(days=2)).isoformat(),
        "deadline_source": "explicit_user",
        "deadline_timezone": "Africa/Lagos",
        "request_text": "Please confirm the date with the clinic.",
        "verification_rule": "The clinic must confirm the date.",
        "idempotency_key": key,
    }

    first = client.post("/api/v1/open-loops", headers=headers, json=first_payload)
    identical = client.post("/api/v1/open-loops", headers=headers, json=first_payload)
    changed_deadline = client.post(
        "/api/v1/open-loops",
        headers=headers,
        json={**first_payload, "deadline_at": (datetime.now(timezone.utc) + timedelta(days=5)).isoformat()},
    )
    changed_evidence = client.post(
        "/api/v1/open-loops",
        headers=headers,
        json={**first_payload, "evidence": [{"kind": "caregiver_report", "text": "A different source report."}]},
    )

    assert first.status_code == 201, first.text
    assert identical.status_code == 201, identical.text
    assert identical.json()["id"] == first.json()["id"]
    assert changed_deadline.status_code == 409, changed_deadline.text
    assert changed_evidence.status_code == 409, changed_evidence.text

    listed = client.get("/api/v1/open-loops", headers=headers)
    assert listed.status_code == 200, listed.text
    assert len(listed.json()["items"]) == 1
