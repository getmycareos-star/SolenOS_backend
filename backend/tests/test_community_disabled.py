import pytest
from fastapi.testclient import TestClient

from app.main import app


def test_community_post_and_reaction_persist_without_writing_to_lcr():
    client = TestClient(app)
    session = client.post(
        "/api/v1/sessions/anonymous",
        json={"name": "Community test care recipient", "caregiver_name": "Community test caregiver"},
    )
    assert session.status_code == 201, session.text
    identity = session.json()
    headers = {"Authorization": f"Bearer {identity['access_token']}"}

    before = client.get(
        f"/api/v1/care-continuity/current-care",
        headers=headers,
    )
    assert before.status_code == 200, before.text

    created = client.post(
        "/api/v1/community",
        headers=headers,
        json={"content": "A peer caregiver experience stored separately from care facts.", "post_type": "experience"},
    )
    assert created.status_code == 201, created.text
    post = created.json()
    assert post["id"]
    assert post["content"] == "A peer caregiver experience stored separately from care facts."

    feed = client.get("/api/v1/community", headers=headers)
    assert feed.status_code == 200, feed.text
    assert any(item["id"] == post["id"] for item in feed.json()["posts"])

    reacted = client.post(
        f"/api/v1/community/{post['id']}/reactions",
        headers=headers,
        json={"reaction_type": "support"},
    )
    assert reacted.status_code == 200, reacted.text

    after = client.get("/api/v1/care-continuity/current-care", headers=headers)
    assert after.status_code == 200, after.text
    before_ids = {item.get("id") for item in before.json().get("items", [])}
    after_ids = {item.get("id") for item in after.json().get("items", [])}
    assert after_ids == before_ids, "Community activity must not create LCR entries"
