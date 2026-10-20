"""Route-table integrity checks for the mounted FastAPI application.

These tests inspect the actual registered app, not just module imports. A router
file existing in the repository is not evidence that the endpoint is reachable.
"""
from collections import defaultdict

from fastapi.routing import APIRoute

from app.main import app
from conftest import APP_ROUTES_BASELINE
from app.api.care_graph import router as care_graph_router
from app.api.daily_care import router as daily_care_router
from app.api.causal import router as causal_router


def test_no_duplicate_http_method_and_path_registrations():
    registrations = defaultdict(list)
    for route in app.router.routes:
        if not isinstance(route, APIRoute):
            continue
        for method in route.methods or set():
            if method in {"HEAD", "OPTIONS"}:
                continue
            registrations[(method, route.path)].append(route.endpoint.__module__ + "." + route.endpoint.__name__)

    duplicates = {
        f"{method} {path}": handlers
        for (method, path), handlers in registrations.items()
        if len(handlers) > 1
    }
    assert not duplicates, "Duplicate route registrations shadow handlers: " + repr(duplicates)


def test_critical_care_continuity_routes_are_mounted():
    # FastAPI's newer router inclusion implementation can expose lazily
    # included routers rather than flattening each child into app.router.routes.
    # OpenAPI is generated from the effective route tree and is the appropriate
    # inventory for checking the externally addressable method/path contract.
    openapi_paths = app.openapi()["paths"]
    registrations = {
        (method.upper(), path)
        for path, operations in openapi_paths.items()
        for method in operations
        if method.lower() in {"get", "post", "put", "patch", "delete", "head", "options"}
    }
    expected = {
        ("POST", "/api/v1/daily-care/entries"),
        ("GET", "/api/v1/graph/{person_id}/snapshot"),
        ("POST", "/api/v1/causal/hypotheses"),
    }
    missing = expected - registrations
    assert not missing, (
        "Critical care-continuity routes are absent from the effective OpenAPI "
        f"contract: {sorted(missing)}; available related paths="
        f"{sorted(path for path in openapi_paths if any(token in path for token in ('graph', 'daily-care', 'causal')))}"
    )
