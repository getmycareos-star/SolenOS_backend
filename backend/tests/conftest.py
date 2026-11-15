"""Keep legacy endpoint tests focused on route logic; security has dedicated tests.

The scoped-session middleware is exercised end-to-end in test_anonymous_sessions.py.
Older route tests predate bearer sessions and intentionally run without that middleware
so they continue to test service/schema behavior rather than fail at the auth boundary.
"""
import pytest
from sqlalchemy import (
    Column, DateTime, ForeignKey, Integer, JSON, String, Table, Text, UniqueConstraint, func,
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


# SQLite create_all() fixtures need the same core LCR tables as the production
# migration chain. These definitions are test-only schema mirrors.
if "care_facts" not in Base.metadata.tables:
    Table(
        "care_facts", Base.metadata,
        Column("id", String(36), primary_key=True),
        Column("person_id", String(36), ForeignKey("persons.id"), nullable=False),
        Column("subject_type", String(80), nullable=False),
        Column("subject_id", String(36)),
        Column("fact_type", String(100), nullable=False),
        Column("fact_value", Text, nullable=False),
        Column("epistemic_status", String(32), nullable=False, server_default="reported"),
        Column("effective_at", DateTime(timezone=True)),
        Column("recorded_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
        Column("superseded_by_fact_id", String(36), ForeignKey("care_facts.id")),
        Column("created_by_caregiver_id", String(36), ForeignKey("caregivers.id")),
        Column("review_notes", Text),
        Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
        Column("updated_at", DateTime(timezone=True)),
    )

if "care_record_change_sets" not in Base.metadata.tables:
    Table(
        "care_record_change_sets", Base.metadata,
        Column("id", String(36), primary_key=True),
        Column("person_id", String(36), ForeignKey("persons.id"), nullable=False),
        Column("care_record_id", String(36)),
        Column("operation", String(60), nullable=False),
        Column("status", String(32), nullable=False, server_default="committed"),
        Column("reason", Text),
        Column("author_caregiver_id", String(36), ForeignKey("caregivers.id")),
        Column("provenance_id", String(36)),
        Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    )

if "care_record_change_items" not in Base.metadata.tables:
    Table(
        "care_record_change_items", Base.metadata,
        Column("id", String(36), primary_key=True),
        Column("person_id", String(36), ForeignKey("persons.id"), nullable=False),
        Column("change_set_id", String(36), ForeignKey("care_record_change_sets.id"), nullable=False),
        Column("target_type", String(100), nullable=False),
        Column("target_id", String(36), nullable=False),
        Column("operation", String(32), nullable=False),
        Column("before_value", JSON),
        Column("after_value", JSON),
        Column("provenance_id", String(36)),
        Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
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
