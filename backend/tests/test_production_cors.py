from fastapi.testclient import TestClient

from app.main import app, cors_origins


PRODUCTION_ORIGIN = "https://solenosai.netlify.app"


def test_production_netlify_origin_is_always_allowlisted():
    assert PRODUCTION_ORIGIN in cors_origins


def test_production_origin_can_preflight_care_write_routes():
    client = TestClient(app)
    for path in ("/api/situation", "/api/lcr/orientation"):
        response = client.options(
            path,
            headers={
                "Origin": PRODUCTION_ORIGIN,
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "authorization,content-type",
            },
        )
        assert response.status_code == 200, f"{path}: {response.status_code} {response.text}"
        assert response.headers.get("access-control-allow-origin") == PRODUCTION_ORIGIN
        assert "POST" in response.headers.get("access-control-allow-methods", "")
