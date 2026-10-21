"""Guard against accidentally exposing sensitive API routes without bearer authentication.

OpenAPI is used because current FastAPI versions may represent included routers with
wrappers rather than exposing every nested APIRoute directly in app.routes.
"""
from app.main import app


PUBLIC_ENDPOINTS = {
    ("/api/v1/auth/signup", "post"),
    ("/api/v1/auth/login", "post"),
    ("/api/v1/auth/logout", "post"),
    ("/health", "get"),
    ("/ready", "get"),
    ("/openapi.json", "get"),
    ("/docs", "get"),
    ("/docs/oauth2-redirect", "get"),
    ("/redoc", "get"),
}


def test_every_non_public_api_operation_requires_a_valid_session():
    schema = app.openapi()
    missing_auth = []
    for path, operations in schema.get("paths", {}).items():
        for method, operation in operations.items():
            if method.lower() in {"head", "options"}:
                continue
            if (path, method.lower()) in PUBLIC_ENDPOINTS:
                continue
            if not operation.get("security"):
                missing_auth.append(f"{method.upper()} {path}")

    assert not missing_auth, (
        "API operations without bearer-session security: " + ", ".join(sorted(missing_auth))
    )
