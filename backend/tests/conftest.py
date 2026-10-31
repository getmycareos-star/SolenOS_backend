"""Keep legacy endpoint tests focused on route logic; security has dedicated tests.

The scoped-session middleware is exercised end-to-end in test_anonymous_sessions.py.
Older route tests predate bearer sessions and intentionally run without that middleware
so they continue to test service/schema behavior rather than fail at the auth boundary.
"""
import pytest

from app.main import app


@pytest.fixture(autouse=True)
def legacy_route_tests_without_session_middleware(request):
    if request.node.get_closest_marker("session_security"):
        yield
        return

    original_middleware = list(app.user_middleware)
    filtered = []
    for middleware in original_middleware:
        dispatch = middleware.kwargs.get("dispatch") if hasattr(middleware, "kwargs") else None
        if getattr(dispatch, "__name__", "") == "enforce_scoped_anonymous_session":
            continue
        filtered.append(middleware)

    app.user_middleware = filtered
    app.middleware_stack = None
    try:
        yield
    finally:
        app.user_middleware = original_middleware
        app.middleware_stack = None
@pytest.fixture(autouse=True)
def companion_model_ready_for_route_tests(request, monkeypatch):
    """Keep route-contract tests independent from the live model runtime.

    Real readiness behavior is covered separately in test_qwen_readiness.py.
    Companion route tests mock model replies and should not make network calls.
    """
    if request.node.get_closest_marker("qwen_readiness"):
        return

    from app.api import collaboration

    monkeypatch.setattr(
        collaboration,
        "qwen_readiness_status",
        lambda *args, **kwargs: {
            "available": True,
            "state": "ready",
            "model": "test-model",
            "failure_reason": None,
        },
    )
