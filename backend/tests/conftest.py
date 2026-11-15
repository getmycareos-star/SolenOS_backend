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
if "living_care_records" not in Base.metadata.tables:
    Table(
        "living_care_records", Base.metadata,
        Column("id", String(36), primary_key=True),
        Column("person_id", String(36), ForeignKey("persons.id"), nullable=False, unique=True),
        Column("record_status", String(32), nullable=False, server_default="active"),
        Column("schema_version", String(40), nullable=False, server_default="lcr.v1"),
        Column("created_at", DateTime(timezone=True)),
        Column("updated_at", DateTime(timezone=True)),
        Column("created_by_caregiver_id", String(36), ForeignKey("caregivers.id")),
    )

if "care_record_versions" not in Base.metadata.tables:
    Table(
        "care_record_versions", Base.metadata,
        Column("id", String(36), primary_key=True),
        Column("care_record_id", String(36), ForeignKey("living_care_records.id"), nullable=False),
        Column("version_number", Integer, nullable=False),
        Column("change_set_id", String(36)),
        Column("record_digest", String(128)),
        Column("snapshot_payload", JSON, nullable=False),
        Column("created_at", DateTime(timezone=True)),
        Column("created_by_caregiver_id", String(36), ForeignKey("caregivers.id")),
        Column("reason", Text),
        UniqueConstraint("care_record_id", "version_number"),
    )

if "care_record_snapshots" not in Base.metadata.tables:
    Table(
        "care_record_snapshots", Base.metadata,
        Column("id", String(36), primary_key=True),
        Column("care_record_id", String(36), ForeignKey("living_care_records.id"), nullable=False),
        Column("version_id", String(36), ForeignKey("care_record_versions.id")),
        Column("as_of", DateTime(timezone=True), nullable=False),
        Column("snapshot_payload", JSON, nullable=False),
        Column("record_digest", String(128)),
        Column("created_at", DateTime(timezone=True)),
        Column("created_by_caregiver_id", String(36), ForeignKey("caregivers.id")),
    )

if "record_provenance" not in Base.metadata.tables:
    Table(
        "record_provenance", Base.metadata,
        Column("id", String(36), primary_key=True),
        Column("person_id", String(36), ForeignKey("persons.id"), nullable=False),
        Column("care_record_id", String(36), ForeignKey("living_care_records.id")),
        Column("source_type", String(80), nullable=False),
        Column("source_id", String(36)),
        Column("author_caregiver_id", String(36), ForeignKey("caregivers.id")),
        Column("author_label", String(240)),
        Column("method", String(80), nullable=False, server_default="manual"),
        Column("recorded_at", DateTime(timezone=True)),
        Column("occurred_at", DateTime(timezone=True)),
        Column("effective_from", DateTime(timezone=True)),
        Column("effective_to", DateTime(timezone=True)),
        Column("reason", Text),
        Column("metadata", JSON),
        Column("created_at", DateTime(timezone=True)),
    )

if "record_provenance_links" not in Base.metadata.tables:
    Table(
        "record_provenance_links", Base.metadata,
        Column("id", String(36), primary_key=True),
        Column("person_id", String(36), ForeignKey("persons.id"), nullable=False),
        Column("provenance_id", String(36), ForeignKey("record_provenance.id"), nullable=False),
        Column("target_type", String(100), nullable=False),
        Column("target_id", String(36), nullable=False),
        Column("relationship", String(40), nullable=False, server_default="originates"),
        Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    )

if "care_record_status_history" not in Base.metadata.tables:
    Table(
        "care_record_status_history", Base.metadata,
        Column("id", String(36), primary_key=True),
        Column("person_id", String(36), ForeignKey("persons.id"), nullable=False),
        Column("care_record_id", String(36), ForeignKey("living_care_records.id"), nullable=False),
        Column("previous_status", String(32)),
        Column("new_status", String(32), nullable=False),
        Column("reason", Text),
        Column("changed_by_caregiver_id", String(36), ForeignKey("caregivers.id")),
        Column("provenance_id", String(36), ForeignKey("record_provenance.id")),
        Column("changed_at", DateTime(timezone=True)),
    )

if "care_decisions" not in Base.metadata.tables:
    Table(
        "care_decisions", Base.metadata,
        Column("id", String(36), primary_key=True),
        Column("person_id", String(36), ForeignKey("persons.id"), nullable=False),
        Column("title", String(240), nullable=False),
        Column("decision_text", Text, nullable=False),
        Column("decision_status", String(40), nullable=False, server_default="proposed"),
        Column("decided_at", DateTime(timezone=True)),
        Column("context", JSON),
        Column("evidence_ids", JSON),
        Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
        Column("created_by_caregiver_id", String(36), ForeignKey("caregivers.id")),
    )

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
