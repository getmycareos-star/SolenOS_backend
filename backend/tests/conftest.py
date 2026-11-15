"""Keep legacy endpoint tests focused on route logic; security has dedicated tests.

The scoped-session middleware is exercised end-to-end in test_anonymous_sessions.py.
Older route tests predate bearer sessions and intentionally run without that middleware
so they continue to test service/schema behavior rather than fail at the auth boundary.
"""
import pytest
from sqlalchemy import (
    Column, DateTime, ForeignKey, Integer, JSON, String, Table, Text, UniqueConstraint,
)

from app.core.database import Base
from app.main import app


# The production migration creates this legacy-compatible revision projection.
# SQLite create_all() tests must declare it centrally rather than depending on
# test_caregiver_context_routes.py being imported first.
if "care_assertion_revisions" not in Base.metadata.tables:
    Table(
        "care_assertion_revisions",
        Base.metadata,
        Column("id", String(36), primary_key=True),
        Column("person_id", String(36), ForeignKey("persons.id"), nullable=False),
        Column("assertion_id", String(36), ForeignKey("care_assertions.id"), nullable=False),
        Column("revision_number", Integer, nullable=False),
        Column("previous_payload", JSON),
        Column("current_payload", JSON),
        Column("revision_reason", Text),
        Column("provenance_id", String(36)),
        Column("changed_by_caregiver_id", String(36), ForeignKey("caregivers.id")),
        Column("changed_at", DateTime(timezone=True)),
        UniqueConstraint("assertion_id", "revision_number"),
    )


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
