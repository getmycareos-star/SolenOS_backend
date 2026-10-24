import importlib

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.database import Base, get_db
from app.models.collaboration import CommunityPost, ConversationMessage

main_module = importlib.import_module("app.main")
app = main_module.app
engine = create_engine(
    "sqlite:///:memory:",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def override_db():
    db = TestingSessionLocal()
    try:
        yield db
    finally:
        db.close()


def setup_client(monkeypatch):
    Base.metadata.create_all(bind=engine)
    app.dependency_overrides[get_db] = override_db
    monkeypatch.setattr(main_module, "SessionLocal", TestingSessionLocal)
    client = TestClient(app)
    first = client.post("/api/v1/sessions/anonymous", json={"caregiver_name": "Family caregiver"})
    second = client.post("/api/v1/sessions/anonymous", json={"caregiver_name": "Other caregiver"})
    assert first.status_code == 201, first.text
    assert second.status_code == 201, second.text
    return (
        client,
        first.json()["access_token"],
        first.json()["person"]["id"],
        first.json()["caregiver"]["id"],
        second.json()["access_token"],
        second.json()["person"]["id"],
    )


def teardown():
    app.dependency_overrides.clear()
    Base.metadata.drop_all(bind=engine)


def test_community_post_feed_reaction_persist_and_session_attribution(monkeypatch):
    client, token, person_id, caregiver_id, other_token, _ = setup_client(monkeypatch)
    try:
        auth = {"Authorization": f"Bearer {token}"}
        forged = client.post("/api/v1/community", headers=auth, json={
            "caregiver_id": "caller-controlled-value",
            "author_type": "solenos_verified",
            "author_label": "Forged verified identity",
            "post_type": "experience",
            "content": "A forged identity must not be accepted.",
        })
        assert forged.status_code == 403, forged.text

        created = client.post("/api/v1/community", headers=auth, json={
            "post_type": "experience",
            "content": "A quiet routine helped us prepare for bedtime.",
            "tags": ["routine", "sleep"],
        })
        assert created.status_code == 201, created.text
        post = created.json()
        assert post["author_type"] == "peer_caregiver"
        assert post["author_label"] == "Anonymous caregiver"

        feed = client.get("/api/v1/community", headers=auth)
        assert feed.status_code == 200
        assert any(row["id"] == post["id"] for row in feed.json()["posts"])

        reaction = client.post(
            f"/api/v1/community/{post['id']}/reactions",
            headers=auth,
            json={"reaction_type": "support"},
        )
        assert reaction.status_code == 200, reaction.text
        assert reaction.json()["reaction_counts"]["support"] == 1

        other_reaction = client.post(
            f"/api/v1/community/{post['id']}/reactions",
            headers={"Authorization": f"Bearer {other_token}"},
            json={"reaction_type": "like"},
        )
        assert other_reaction.status_code == 200, other_reaction.text
        db = TestingSessionLocal()
        assert db.query(CommunityPost).filter_by(id=post["id"]).count() == 1
        db.close()
    finally:
        teardown()


def test_companion_emergency_response_is_scoped_and_persisted_without_model(monkeypatch):
    client, token, person_id, _, other_token, other_person_id = setup_client(monkeypatch)
    try:
        auth = {"Authorization": f"Bearer {token}"}
        denied = client.post("/api/v1/conversation/message", headers=auth, json={
            "person_id": other_person_id,
            "text": "What happened today?",
        })
        assert denied.status_code == 403, denied.text

        response = client.post("/api/v1/conversation/message", headers=auth, json={
            "person_id": person_id,
            "text": "They are unconscious and not breathing",
        })
        assert response.status_code == 200, response.text
        data = response.json()
        assert data["guard_verdict"] == "blocked"
        assert "urgent medical help" in data["reply"].lower()
        assert data["ai_generated"] is False
        assert len(data["messages"]) == 2

        history = client.get(f"/api/v1/conversation/{data['session_id']}", headers=auth)
        assert history.status_code == 200
        assert len(history.json()["messages"]) == 2

        cross_record = client.get(
            f"/api/v1/conversation/{data['session_id']}",
            headers={"Authorization": f"Bearer {other_token}"},
        )
        assert cross_record.status_code == 404, cross_record.text

        db = TestingSessionLocal()
        rows = db.query(ConversationMessage).filter_by(session_id=data["session_id"]).all()
        assert [row.role for row in rows] == ["user", "assistant"]
        db.close()
    finally:
        teardown()



def test_original_upload_download_and_cross_record_denial(monkeypatch):
    client, token, other_token = setup_client(monkeypatch)
    try:
        auth = {"Authorization": f"Bearer {token}"}
        original = b"test-image-bytes"
        uploaded = client.post(
            "/api/v1/upload",
            headers=auth,
            data={"person_id": "contract-person-1", "caregiver_id": "contract-caregiver-1"},
            files={"file": ("medication.png", original, "image/png")},
        )
        assert uploaded.status_code == 201, uploaded.text
        evidence_id = uploaded.json()["evidence_id"]
        assert uploaded.json()["persisted"] is True

        downloaded = client.get(f"/api/v1/upload/{evidence_id}", headers=auth)
        assert downloaded.status_code == 200
        assert downloaded.content == original
        assert downloaded.headers["content-type"] == "image/png"

        denied = client.get(
            f"/api/v1/upload/{evidence_id}",
            headers={"Authorization": f"Bearer {other_token}"},
        )
        assert denied.status_code == 404
    finally:
        teardown()



def test_care_delta_routes_bind_person_and_caregiver_to_session(monkeypatch):
    client, token, other_token = setup_client(monkeypatch)
    try:
        import app.api.care_delta as care_delta_api
        monkeypatch.setattr(care_delta_api, "reconcile_event", lambda db, **kwargs: {
            "event_summary": "Caregiver report reconciled.",
            "caregiver_summary": "Review the source before acting.",
            "clinician_summary": "",
            "medications": [], "conditions": [], "symptoms": [], "tests": [],
            "follow_ups": [], "contradictions": [], "clinical_gaps": [],
            "persisted": bool(kwargs["persist"]),
            "created_by_caregiver_id": kwargs["created_by_caregiver_id"],
        })
        auth = {"Authorization": f"Bearer {token}"}
        ok = client.post("/api/v1/care-delta/reconcile", headers=auth, json={
            "person_id": "contract-person-1",
            "event_date": "2026-10-24T09:00:00Z",
            "source": "Caregiver report",
            "content": "Care recipient slept poorly last night.",
            "evidence_ids": [],
            "persist": True,
        })
        assert ok.status_code == 200, ok.text
        assert ok.json()["persisted"] is True
        assert ok.json()["created_by_caregiver_id"] == "contract-caregiver-1"

        denied = client.post("/api/v1/care-delta/reconcile", headers=auth, json={
            "person_id": "contract-person-2",
            "source": "Caregiver report",
            "content": "Cross-record access must fail.",
        })
        assert denied.status_code == 403

        history = client.get("/api/v1/care-delta/runs/contract-person-2", headers=auth)
        assert history.status_code == 403
        other_history = client.get(
            "/api/v1/care-delta/runs/contract-person-1",
            headers={"Authorization": f"Bearer {other_token}"},
        )
        assert other_history.status_code == 403
    finally:
        teardown()
