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


def test_rejected_preflight_logs_origin_and_requested_headers_without_request_data():
    from unittest.mock import patch

    client = TestClient(app)
    with patch("app.main.logger.warning") as warning:
        response = client.options(
            "/api/situation",
            headers={
                "Origin": "https://unrecognized.invalid",
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "authorization,content-type",
            },
        )

    assert response.status_code == 400
    warning.assert_called_once()
    message, *metadata = warning.call_args.args
    assert message.startswith("Rejected browser preflight")
    assert metadata == [
        "/api/situation",
        "https://unrecognized.invalid",
        "POST",
        "authorization,content-type",
        400,
    ]
    # The logger receives header names only, never an Authorization value or
    # request body/care record data.
    assert "Authorization:" not in repr(warning.call_args)
