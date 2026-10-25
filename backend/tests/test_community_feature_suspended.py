"""Regression tests for the reversible Community feature suspension."""

from fastapi.testclient import TestClient

from app.main import app


def test_community_feed_is_disabled_even_without_a_session():
    response = TestClient(app).get("/api/v1/community")
    assert response.status_code == 404
    assert response.json()["detail"] == "Community is currently unavailable"


def test_community_post_creation_is_disabled_at_the_api_boundary():
    response = TestClient(app).post(
        "/api/v1/community",
        json={"content": "This must not be stored"},
    )
    assert response.status_code == 404
    assert response.json()["detail"] == "Community is currently unavailable"


def test_community_reactions_are_disabled_at_the_api_boundary():
    response = TestClient(app).post(
        "/api/v1/community/post-id/reactions",
        json={"reaction_type": "support"},
    )
    assert response.status_code == 404
    assert response.json()["detail"] == "Community is currently unavailable"


def test_community_assistant_workflow_is_disabled_without_a_session():
    response = TestClient(app).post(
        "/api/v1/assistant/assist",
        json={"workflow": "community", "text": "Summarize peer experiences"},
    )
    assert response.status_code == 404
    assert response.json()["detail"] == "Community is currently unavailable"
