"""Route-table integrity checks for the mounted FastAPI application.

These tests inspect the actual registered app, not just module imports. A router
file existing in the repository is not evidence that the endpoint is reachable.
"""
from collections import defaultdict

from fastapi.routing import APIRoute

from app.main import app
from conftest import APP_ROUTES_BASELINE


def test_no_duplicate_http_method_and_path_registrations():
    registrations = defaultdict(list)
    for route in app.routes:
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
    registrations = {
        (method, route.path)
        for route in app.routes
        if isinstance(route, APIRoute)
        for method in (route.methods or set())
    }
    expected = {
        ("POST", "/api/v1/daily-care/entries"),
        ("GET", "/api/v1/graph/{person_id}/snapshot"),
        ("POST", "/api/v1/causal/hypotheses"),
    }
    missing = expected - registrations
    if missing:
        baseline_routes = sorted(
            (method, getattr(route, "path", repr(route)))
            for route in APP_ROUTES_BASELINE
            if isinstance(route, APIRoute)
            for method in (route.methods or set())
        )
        related_routes = sorted(
            (method, route.path, route.endpoint.__module__ + "." + route.endpoint.__name__)
            for route in app.routes
            if isinstance(route, APIRoute)
            and any(token in route.path for token in ("graph", "daily-care", "causal"))
            for method in (route.methods or set())
        )
        raise AssertionError(
            "Critical care-continuity routes are not mounted: "
            f"{sorted(missing)}; baseline_has_expected={expected.issubset(set(baseline_routes))}; "
            f"related baseline routes={[r for r in baseline_routes if any(t in r[1] for t in ('graph', 'daily-care', 'causal'))]}; "
            f"related current routes={related_routes}"
        )
