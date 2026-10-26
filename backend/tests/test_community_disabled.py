import pytest
from fastapi.testclient import TestClient

from app.main import app


@pytest.mark.parametrize(
    ("method", "path", "payload"),
    [
        ("get", "/api/v1/community", None),
        ("post", "/api/v1/community", {"content": "must not be published", "post_type": "experience"}),
        ("get", "/api/v1/community/experience/summary", None),
        ("post", "/api/v1/community/123/reactions", {"reaction_type": "support"}),
    ],
)
def test_community_api_routes_are_not_registered(method, path, payload):
    client = TestClient(app)
    session = client.post(
        "/api/v1/sessions/anonymous",
        json={"name": "Care recipient", "caregiver_name": "Anonymous caregiver"},
    )
    assert session.status_code == 201, session.text
    response = getattr(client, method)(
        path,
        json=payload if payload is not None else None,
        headers={"Authorization": f"Bearer {session.json()['access_token']}"},
    )
    assert response.status_code == 404, response.text
