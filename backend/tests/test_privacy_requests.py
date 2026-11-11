from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.main as main_module
from app.core.database import Base, get_db, SessionLocal as DefaultSessionLocal
from app.main import app

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


def create_session(client, name):
    response = client.post(
        "/api/v1/sessions/anonymous",
        json={"name": name, "caregiver_name": "Caregiver"},
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_privacy_request_requires_authenticated_session():
    client = TestClient(app)
    response = client.post(
        "/api/v1/privacy/requests",
        json={
            "request_type": "deletion",
            "authority_basis": "self",
            "authority_attested": True,
        },
    )
    assert response.status_code == 401


def test_privacy_request_is_pending_and_does_not_claim_fulfilment():
    client = TestClient(app)
    session = create_session(client, "Care recipient")
    headers = {"Authorization": f"Bearer {session['access_token']}"}

    response = client.post(
        "/api/v1/privacy/requests",
        headers=headers,
        json={
            "request_type": "deletion",
            "authority_basis": "authorized_caregiver",
            "authority_attested": True,
            "note": "Please review this request.",
        },
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["status"] == "received_pending_authority_review"
    assert body["authority_verified"] is False
    assert body["fulfilment_started"] is False

    listed = client.get("/api/v1/privacy/requests", headers=headers)
    assert listed.status_code == 200, listed.text
    assert [item["id"] for item in listed.json()["requests"]] == [body["id"]]

    from app.models.care import Person
    db = TestSessionLocal()
    try:
        assert db.query(Person).filter(Person.id == session["person"]["id"]).count() == 1
    finally:
        db.close()


def test_privacy_request_list_is_scoped_to_the_requesting_caregiver_and_record():
    client = TestClient(app)
    first = create_session(client, "First recipient")
    second = create_session(client, "Second recipient")
    first_headers = {"Authorization": f"Bearer {first['access_token']}"}
    second_headers = {"Authorization": f"Bearer {second['access_token']}"}

    submitted = client.post(
        "/api/v1/privacy/requests",
        headers=first_headers,
        json={
            "request_type": "access",
            "authority_basis": "self",
            "authority_attested": True,
        },
    )
    assert submitted.status_code == 201, submitted.text
    own = client.get("/api/v1/privacy/requests", headers=first_headers)
    other = client.get("/api/v1/privacy/requests", headers=second_headers)
    assert len(own.json()["requests"]) == 1
    assert other.json()["requests"] == []


def test_privacy_request_rejects_unattested_authority():
    client = TestClient(app)
    session = create_session(client, "Care recipient")
    response = client.post(
        "/api/v1/privacy/requests",
        headers={"Authorization": f"Bearer {session['access_token']}"},
        json={
            "request_type": "export",
            "authority_basis": "other",
            "authority_attested": False,
        },
    )
    assert response.status_code == 422


from app.models.privacy_delivery import AuditLogEvent


def test_privacy_review_requires_configured_reviewer_credential(monkeypatch):
    monkeypatch.setenv("SOLENOS_PRIVACY_REVIEWER_TOKEN", "r" * 40)
    monkeypatch.setenv("SOLENOS_PRIVACY_REVIEWER_ID", "founder_privacy_reviewer")
    client = TestClient(app)
    session = create_session(client, "Review recipient")
    headers = {"Authorization": f"Bearer {session['access_token']}"}
    submitted = client.post(
        "/api/v1/privacy/requests",
        headers=headers,
        json={
            "request_type": "deletion",
            "authority_basis": "legal_representative",
            "authority_attested": True,
        },
    )
    assert submitted.status_code == 201, submitted.text
    request_id = submitted.json()["id"]

    denied = client.get("/api/v1/privacy/requests/reviewer/pending", headers=headers)
    assert denied.status_code == 401

    reviewer_headers = {
        **headers,
        "X-SolenOS-Privacy-Reviewer-Token": "r" * 40,
    }
    pending = client.get("/api/v1/privacy/requests/reviewer/pending", headers=reviewer_headers)
    assert pending.status_code == 200, pending.text
    assert [item["id"] for item in pending.json()["requests"]] == [request_id]
    assert pending.json()["reviewer_id"] == "founder_privacy_reviewer"

    reviewed = client.post(
        f"/api/v1/privacy/requests/{request_id}/review",
        headers=reviewer_headers,
        json={
            "decision": "approve",
            "review_basis": "Reviewed the submitted identity and authority evidence.",
            "authority_evidence_checked": "Government-issued identity and signed legal authority document.",
        },
    )
    assert reviewed.status_code == 200, reviewed.text
    body = reviewed.json()
    assert body["status"] == "approved_pending_fulfilment_assessment"
    assert body["authority_verified_by_reviewer"] is True
    assert body["fulfilment_started"] is False
    assert body["resolved_at"] is None
    assert "no data was exported" in body["message"]

    # Approval removes the request from the pending queue but does not mark it
    # fulfilled or resolved.
    after_approval = client.get(
        "/api/v1/privacy/requests/reviewer/pending",
        headers=reviewer_headers,
    )
    assert after_approval.status_code == 200, after_approval.text
    assert request_id not in [item["id"] for item in after_approval.json()["requests"]]

    duplicate = client.post(
        f"/api/v1/privacy/requests/{request_id}/review",
        headers=reviewer_headers,
        json={
            "decision": "approve",
            "review_basis": "Attempted duplicate review.",
            "authority_evidence_checked": "Same evidence.",
        },
    )
    assert duplicate.status_code == 409

    db = TestSessionLocal()
    try:
        event = (
            db.query(AuditLogEvent)
            .filter(
                AuditLogEvent.target_id == request_id,
                AuditLogEvent.action == "privacy_request.reviewed",
            )
            .one()
        )
        assert event.actor_id == "founder_privacy_reviewer"
        assert event.outcome == "approve"
    finally:
        db.close()


def test_privacy_review_approval_requires_authority_evidence(monkeypatch):
    monkeypatch.setenv("SOLENOS_PRIVACY_REVIEWER_TOKEN", "s" * 40)
    monkeypatch.setenv("SOLENOS_PRIVACY_REVIEWER_ID", "founder_privacy_reviewer")
    client = TestClient(app)
    session = create_session(client, "Evidence recipient")
    headers = {"Authorization": f"Bearer {session['access_token']}"}
    submitted = client.post(
        "/api/v1/privacy/requests",
        headers=headers,
        json={
            "request_type": "export",
            "authority_basis": "self",
            "authority_attested": True,
        },
    )
    assert submitted.status_code == 201, submitted.text

    response = client.post(
        f"/api/v1/privacy/requests/{submitted.json()['id']}/review",
        headers={
            **headers,
            "X-SolenOS-Privacy-Reviewer-Token": "s" * 40,
        },
        json={
            "decision": "approve",
            "review_basis": "Reviewed the available request.",
        },
    )
    assert response.status_code == 422


def test_privacy_reviewer_fails_closed_when_configuration_is_missing(monkeypatch):
    monkeypatch.delenv("SOLENOS_PRIVACY_REVIEWER_TOKEN", raising=False)
    monkeypatch.delenv("SOLENOS_PRIVACY_REVIEWER_ID", raising=False)
    response = TestClient(app).get("/api/v1/privacy/requests/reviewer/pending")
    assert response.status_code == 503


def test_privacy_reviewer_rejects_wrong_and_short_credentials(monkeypatch):
    monkeypatch.setenv("SOLENOS_PRIVACY_REVIEWER_TOKEN", "v" * 40)
    monkeypatch.setenv("SOLENOS_PRIVACY_REVIEWER_ID", "privacy-reviewer")
    client = TestClient(app)
    for token in ("wrong-token", "v" * 31, ""):
        response = client.get(
            "/api/v1/privacy/requests/reviewer/pending",
            headers={"X-SolenOS-Privacy-Reviewer-Token": token},
        )
        assert response.status_code == 401, response.text


def test_privacy_review_rejection_is_audited_and_removed_from_pending(monkeypatch):
    monkeypatch.setenv("SOLENOS_PRIVACY_REVIEWER_TOKEN", "w" * 40)
    monkeypatch.setenv("SOLENOS_PRIVACY_REVIEWER_ID", "privacy-reviewer")
    client = TestClient(app)
    session = create_session(client, "Rejection recipient")
    submitted = client.post(
        "/api/v1/privacy/requests",
        headers={"Authorization": f"Bearer {session['access_token']}"},
        json={
            "request_type": "correction",
            "authority_basis": "other",
            "authority_attested": True,
        },
    )
    assert submitted.status_code == 201, submitted.text
    request_id = submitted.json()["id"]
    reviewer_headers = {"X-SolenOS-Privacy-Reviewer-Token": "w" * 40}

    rejected = client.post(
        f"/api/v1/privacy/requests/{request_id}/review",
        headers=reviewer_headers,
        json={
            "decision": "reject",
            "review_basis": "The submitted authority evidence was insufficient.",
        },
    )
    assert rejected.status_code == 200, rejected.text
    body = rejected.json()
    assert body["status"] == "rejected"
    assert body["authority_verified_by_reviewer"] is False
    assert body["fulfilment_started"] is False
    assert "no data was exported" in body["message"]

    pending = client.get(
        "/api/v1/privacy/requests/reviewer/pending",
        headers=reviewer_headers,
    )
    assert pending.status_code == 200, pending.text
    assert request_id not in [item["id"] for item in pending.json()["requests"]]

    db = TestSessionLocal()
    try:
        event = (
            db.query(AuditLogEvent)
            .filter(
                AuditLogEvent.target_id == request_id,
                AuditLogEvent.action == "privacy_request.reviewed",
            )
            .one()
        )
        assert event.actor_id == "privacy-reviewer"
        assert event.outcome == "reject"
    finally:
        db.close()


def test_privacy_request_accepts_each_supported_request_type(monkeypatch):
    client = TestClient(app)
    session = create_session(client, "Request type recipient")
    headers = {"Authorization": f"Bearer {session['access_token']}"}
    for request_type in ("access", "export", "correction", "deletion"):
        response = client.post(
            "/api/v1/privacy/requests",
            headers=headers,
            json={
                "request_type": request_type,
                "authority_basis": "self",
                "authority_attested": True,
            },
        )
        assert response.status_code == 201, response.text
        assert response.json()["request_type"] == request_type
        assert response.json()["fulfilment_started"] is False

def test_privacy_review_rejects_invalid_reviewer_and_unknown_request(monkeypatch):
    monkeypatch.setenv("SOLENOS_PRIVACY_REVIEWER_TOKEN", "x" * 40)
    monkeypatch.setenv("SOLENOS_PRIVACY_REVIEWER_ID", "privacy-reviewer")
    client = TestClient(app)
    session = create_session(client, "Reviewer authorization recipient")
    submitted = client.post(
        "/api/v1/privacy/requests",
        headers={"Authorization": f"Bearer {session['access_token']}"},
        json={
            "request_type": "access",
            "authority_basis": "self",
            "authority_attested": True,
        },
    )
    assert submitted.status_code == 201, submitted.text
    request_id = submitted.json()["id"]

    payload = {
        "decision": "approve",
        "review_basis": "The request was reviewed.",
        "authority_evidence_checked": "Identity evidence was checked.",
    }
    wrong_token = client.post(
        f"/api/v1/privacy/requests/{request_id}/review",
        headers={"X-SolenOS-Privacy-Reviewer-Token": "wrong"},
        json=payload,
    )
    assert wrong_token.status_code == 401, wrong_token.text

    reviewer_headers = {"X-SolenOS-Privacy-Reviewer-Token": "x" * 40}
    unknown = client.post(
        "/api/v1/privacy/requests/not-a-real-request/review",
        headers=reviewer_headers,
        json=payload,
    )
    assert unknown.status_code == 404, unknown.text

    still_pending = client.get(
        "/api/v1/privacy/requests/reviewer/pending",
        headers=reviewer_headers,
    )
    assert still_pending.status_code == 200, still_pending.text
    assert [item["id"] for item in still_pending.json()["requests"]] == [request_id]


def test_privacy_reviewer_queue_excludes_request_notes_and_non_pending_items(monkeypatch):
    monkeypatch.setenv("SOLENOS_PRIVACY_REVIEWER_TOKEN", "y" * 40)
    monkeypatch.setenv("SOLENOS_PRIVACY_REVIEWER_ID", "privacy-reviewer")
    client = TestClient(app)
    session = create_session(client, "Reviewer queue privacy recipient")
    caregiver_headers = {"Authorization": f"Bearer {session['access_token']}"}
    reviewer_headers = {"X-SolenOS-Privacy-Reviewer-Token": "y" * 40}

    submitted = client.post(
        "/api/v1/privacy/requests",
        headers=caregiver_headers,
        json={
            "request_type": "correction",
            "authority_basis": "self",
            "authority_attested": True,
            "note": "PRIVATE_REQUEST_NOTE_MUST_NOT_APPEAR_IN_REVIEW_QUEUE",
        },
    )
    assert submitted.status_code == 201, submitted.text
    request_id = submitted.json()["id"]

    pending = client.get(
        "/api/v1/privacy/requests/reviewer/pending",
        headers=reviewer_headers,
    )
    assert pending.status_code == 200, pending.text
    assert request_id in [item["id"] for item in pending.json()["requests"]]
    assert "PRIVATE_REQUEST_NOTE_MUST_NOT_APPEAR_IN_REVIEW_QUEUE" not in pending.text

    rejected = client.post(
        f"/api/v1/privacy/requests/{request_id}/review",
        headers=reviewer_headers,
        json={
            "decision": "reject",
            "review_basis": "The submitted authority evidence was insufficient.",
        },
    )
    assert rejected.status_code == 200, rejected.text

    after_rejection = client.get(
        "/api/v1/privacy/requests/reviewer/pending",
        headers=reviewer_headers,
    )
    assert after_rejection.status_code == 200, after_rejection.text
    assert request_id not in [item["id"] for item in after_rejection.json()["requests"]]

