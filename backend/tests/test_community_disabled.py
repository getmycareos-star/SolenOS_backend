import pytest
from fastapi.testclient import TestClient

from app.main import app


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("get", "/api/v1/community"),
        ("post", "/api/v1/community"),
        ("get", "/api/v1/community/experience/summary"),
        ("post", "/api/v1/community/123/reactions"),
    ],
)
def test_community_api_is_disabled_without_authentication(method, path):
    response = getattr(TestClient(app), method)(path)
    assert response.status_code == 404, response.text
    assert response.json() == {"detail": "Community is currently disabled"}
