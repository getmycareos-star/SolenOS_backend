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
    registrations = {
        (method, route.path)
        for route in app.router.routes
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
        raw_related_baseline = sorted(
            (type(route).__module__ + "." + type(route).__name__, getattr(route, "path", None))
            for route in APP_ROUTES_BASELINE
            if any(token in str(getattr(route, "path", "")) for token in ("graph", "daily-care", "causal"))
        )
        raw_related_current = sorted(
            (type(route).__module__ + "." + type(route).__name__, getattr(route, "path", None))
            for route in app.router.routes
            if any(token in str(getattr(route, "path", "")) for token in ("graph", "daily-care", "causal"))
        )
        router_counts = {
            "care_graph": len(care_graph_router.routes),
            "daily_care": len(daily_care_router.routes),
            "causal": len(causal_router.routes),
        }
        raise AssertionError(
            "Critical care-continuity routes are not mounted: "
            f"{sorted(missing)}; baseline_has_expected={expected.issubset(set(baseline_routes))}; "
            f"router_counts={router_counts}; raw_related_baseline={raw_related_baseline}; "
            f"raw_related_current={raw_related_current}; "
            f"typed_related_baseline={[r for r in baseline_routes if any(t in r[1] for t in ('graph', 'daily-care', 'causal'))]}"
        )
