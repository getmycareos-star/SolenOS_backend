from fastapi.testclient import TestClient

from app.main import app


def test_cors_preflight_allows_netlify_credentials_and_required_headers():
    client = TestClient(app)
    response = client.options(
        "/api/situation",
        headers={
            "Origin": "https://solenosai.netlify.app",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "authorization,content-type",
        },
    )
    assert response.status_code == 200, response.text
    assert response.headers["access-control-allow-origin"] == "https://solenosai.netlify.app"
    assert response.headers["access-control-allow-credentials"] == "true"
    assert "POST" in response.headers["access-control-allow-methods"]
    allowed_headers = response.headers["access-control-allow-headers"].lower()
    assert "authorization" in allowed_headers
    assert "content-type" in allowed_headers


def test_cors_preflight_rejects_unlisted_origin():
    client = TestClient(app)
    response = client.options(
        "/api/situation",
        headers={
            "Origin": "https://not-solenos.example",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "authorization,content-type",
        },
    )
    assert response.status_code == 400
    assert "access-control-allow-origin" not in response.headers
