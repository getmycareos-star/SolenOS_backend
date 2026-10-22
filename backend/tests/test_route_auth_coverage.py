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

def test_action_routes_match_the_frontend_contract():
    paths = app.openapi()["paths"]
    expected = {
        ("/api/v1/actions", "post"),
        ("/api/v1/actions/{person_id}", "get"),
        ("/api/v1/actions/{action_id}/complete", "post"),
        ("/api/v1/actions/safety-check", "post"),
        ("/api/v1/actions/documents/{document_id}/extract-actions", "post"),
    }
    missing = [
        f"{method.upper()} {path}"
        for path, method in expected
        if path not in paths or method not in paths[path]
    ]
    nested_prefixes = [path for path in paths if path.startswith("/api/v1/actions/actions")]
    assert not missing, "Missing frontend action API routes: " + ", ".join(sorted(missing))
    assert not nested_prefixes, "Duplicate nested action prefix remains: " + ", ".join(nested_prefixes)

