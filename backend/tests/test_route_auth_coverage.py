"""Guard against accidentally exposing care API routes without session authentication.

Only signup/login/logout and FastAPI's public diagnostics/docs are intentionally
unauthenticated. The time utility routes are mounted under the authenticated API router
and must retain that dependency unless they are deliberately moved to a public router.
"""
from fastapi.routing import APIRoute

from app.api.authentication import current_account
from app.main import app


PUBLIC_ENDPOINTS = {
    ("/api/v1/auth/signup", "POST"),
    ("/api/v1/auth/login", "POST"),
    ("/api/v1/auth/logout", "POST"),
    ("/health", "GET"),
    ("/ready", "GET"),
    ("/openapi.json", "GET"),
    ("/docs", "GET"),
    ("/docs/oauth2-redirect", "GET"),
    ("/redoc", "GET"),
}


def _dependency_tree_contains(dependant, expected_call) -> bool:
    if dependant.call is expected_call:
        return True
    return any(
        _dependency_tree_contains(child, expected_call)
        for child in dependant.dependencies
    )


def test_every_non_public_api_route_requires_a_valid_session():
    missing_auth = []
    for route in app.routes:
        if not isinstance(route, APIRoute):
            continue
        for method in route.methods or set():
            if method in {"HEAD", "OPTIONS"}:
                continue
            if (route.path, method) in PUBLIC_ENDPOINTS:
                continue
            if not _dependency_tree_contains(route.dependant, current_account):
                missing_auth.append(f"{method} {route.path}")

    assert not missing_auth, (
        "API routes without current_account authentication dependency: "
        + ", ".join(sorted(missing_auth))
    )
