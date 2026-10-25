import uuid
from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)


def test_review_gate_and_professional_post_reaches_caregiver_feed(monkeypatch):
    email = "professional-" + str(uuid.uuid4()) + "@example.test"
    created = client.post("/api/v1/professional-auth/register", json={
        "email": email, "password": "professional-long-password",
        "full_name": "Avery Example", "professional_title": "Dementia Care Educator",
    })
    assert created.status_code == 201, created.text
    profile = created.json()["professional"]
    headers = {"Authorization": "Bearer " + created.json()["access_token"]}
    payload = {
        "title": "Calmer evening routines",
        "body": "Keep evening routines predictable, reduce competing noise, and record what helped so another caregiver can follow the same approach.",
        "contribution_type": "knowledge",
    }
    denied = client.post("/api/v1/professional-auth/contributions", json=payload, headers=headers)
    assert denied.status_code == 403, denied.text

    monkeypatch.setenv("SOLENOS_TEAM_REVIEW_TOKEN", "local-review-key")
    reviewed = client.post("/api/v1/professional-auth/review/" + profile["id"],
        json={"status": "verified"}, headers={"X-SolenOS-Team-Key": "local-review-key"})
    assert reviewed.status_code == 200, reviewed.text

    published = client.post("/api/v1/professional-auth/contributions", json=payload, headers=headers)
    assert published.status_code == 201, published.text
    caregiver = client.post("/api/v1/sessions/anonymous", json={"name": "Test care recipient"})
    feed = client.get("/api/v1/community", headers={"Authorization": "Bearer " + caregiver.json()["access_token"]})
    assert feed.status_code == 200, feed.text
    match = next((post for post in feed.json()["posts"] if post["id"] == published.json()["id"]), None)
    assert match is not None
    assert match["author_type"] == "professional"
    assert match["title"] == payload["title"]
    assert match["provenance_source"] == "verified_professional"


def test_team_review_requires_secret(monkeypatch):
    email = "review-" + str(uuid.uuid4()) + "@example.test"
    created = client.post("/api/v1/professional-auth/register", json={
        "email": email, "password": "review-long-password",
        "full_name": "Review Example", "professional_title": "Social Worker",
    })
    monkeypatch.setenv("SOLENOS_TEAM_REVIEW_TOKEN", "local-review-key")
    response = client.post("/api/v1/professional-auth/review/" + created.json()["professional"]["id"],
        json={"status": "verified"})
    assert response.status_code == 403, response.text
