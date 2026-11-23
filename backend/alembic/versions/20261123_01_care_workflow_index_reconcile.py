"""Reconcile missing single-column indexes in the production ORM contract.

Revision ID: 20261123_01_care_workflow_index_reconcile
Revises: 20261122_01_qwen_job_event_update_guard

The live schema audit identified missing lookup indexes across evidence,
handoff, notification, privacy and appointment workflow tables. This migration
adds only indexes that are not already present with the exact column shape.
It does not rewrite or delete care data.
"""
from __future__ import annotations

import hashlib

from alembic import op
from sqlalchemy import inspect

revision = "20261123_01_care_workflow_index_reconcile"
down_revision = "20261122_01_qwen_job_event_update_guard"
branch_labels = None
depends_on = None

INDEXES = {
    "delivery_failure_events": ("delivery_id", "recipient_id", "resolved_at"),
    "care_assertions": ("source_conversation_id",),
    "evidence_claim_assessments": ("claim_id", "person_id"),
    "evidence_claim_dependencies": ("person_id",),
    "evidence_source_relationships": ("person_id",),
    "evidence_observations": ("evidence_id", "person_id"),
    "evidence_completeness_policies": ("person_id",),
    "evidence_completeness_assessments": ("person_id",),
    "handoff_records": ("expires_at",),
    "handoff_recipients": ("handoff_id", "recipient_id"),
    "handoff_acknowledgements": ("handoff_id", "recipient_id"),
    "handoff_access_logs": ("handoff_id", "recipient_id"),
    "notification_read_receipts": ("notification_id", "reader_id"),
    "data_subject_requests": ("requested_at", "requester_id"),
    "appointment_workspace_records": ("person_id",),
    "appointment_workspace_revisions": ("person_id", "record_id"),
    "ai_output_lcr_proposals": ("source_message_id",),
}


def _index_name(table_name: str, column_name: str) -> str:
    digest = hashlib.sha256(f"{table_name}.{column_name}".encode("utf-8")).hexdigest()[:20]
    return f"ix_solenos_schema_{digest}"


def _has_required_index(bind, table_name: str, column_name: str) -> bool:
    return any(
        tuple(index.get("column_names") or ()) == (column_name,)
        and not bool(index.get("unique", False))
        for index in inspect(bind).get_indexes(table_name)
    )


def _create_missing_index(bind, table_name: str, column_name: str) -> str | None:
    inspector = inspect(bind)
    if not inspector.has_table(table_name):
        raise RuntimeError(f"Cannot reconcile {table_name}.{column_name}: table is missing")

    columns = {column["name"] for column in inspector.get_columns(table_name)}
    if column_name not in columns:
        raise RuntimeError(f"Cannot reconcile {table_name}.{column_name}: column is missing")

    if _has_required_index(bind, table_name, column_name):
        return None

    # Index names are schema-global in PostgreSQL. Use a deterministic,
    # table-and-column-specific name and fail rather than silently accepting
    # a same-name index attached to a different table or column.
    index_name = _index_name(table_name, column_name)
    existing_names = {
        index.get("name")
        for existing_table in inspector.get_table_names()
        for index in inspector.get_indexes(existing_table)
        if index.get("name")
    }
    if index_name in existing_names:
        raise RuntimeError(
            f"Cannot safely create {table_name}.{column_name}: "
            f"index name {index_name} is already used by another index"
        )

    op.create_index(index_name, table_name, [column_name], unique=False)
    if not _has_required_index(bind, table_name, column_name):
        raise RuntimeError(f"Index verification failed for {table_name}.{column_name}")
    return index_name


def upgrade() -> None:
    bind = op.get_bind()
    for table_name, columns in INDEXES.items():
        for column_name in columns:
            _create_missing_index(bind, table_name, column_name)


def downgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    for table_name, columns in reversed(tuple(INDEXES.items())):
        if not inspector.has_table(table_name):
            continue
        expected_columns = set(columns)
        for index in inspector.get_indexes(table_name):
            name = str(index.get("name") or "")
            indexed_columns = tuple(index.get("column_names") or ())
            if (
                name.startswith("ix_solenos_schema_")
                and len(indexed_columns) == 1
                and indexed_columns[0] in expected_columns
                and not bool(index.get("unique", False))
            ):
                op.drop_index(name, table_name=table_name)
