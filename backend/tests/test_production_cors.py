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


def test_rejected_preflight_logs_origin_and_requested_headers_without_request_data(caplog):
    import logging

    client = TestClient(app)
    with caplog.at_level(logging.WARNING):
        response = client.options(
            "/api/situation",
            headers={
                "Origin": "https://unrecognized.invalid",
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "authorization,content-type",
            },
        )
    assert response.status_code == 400
    assert "Rejected browser preflight" in caplog.text
    assert "https://unrecognized.invalid" in caplog.text
    assert "requested_method=POST" in caplog.text
    assert "requested_headers=authorization,content-type" in caplog.text
    assert "Authorization:" not in caplog.text
