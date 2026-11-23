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
from sqlalchemy import inspect, text

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


def _is_usable_lookup_index(index: dict, column_name: str) -> bool:
    dialect_options = index.get("dialect_options") or {}
    has_predicate = bool(index.get("filter_definition")) or any(
        bool(value)
        for key, value in dialect_options.items()
        if str(key).endswith("_where")
    )
    return (
        tuple(index.get("column_names") or ()) == (column_name,)
        and not bool(index.get("unique", False))
        and not has_predicate
    )


def _valid_postgres_index_names(bind) -> set[str] | None:
    """Return only indexes PostgreSQL can actually use for query plans."""
    if bind.dialect.name != "postgresql":
        return None
    rows = bind.execute(text("""
        SELECT index_class.relname
        FROM pg_index AS index_data
        JOIN pg_class AS index_class ON index_class.oid = index_data.indexrelid
        JOIN pg_class AS table_class ON table_class.oid = index_data.indrelid
        JOIN pg_namespace AS table_schema ON table_schema.oid = table_class.relnamespace
        WHERE table_schema.nspname = current_schema()
          AND index_data.indisvalid
          AND index_data.indisready
    """)).scalars()
    return {str(name) for name in rows}


def _has_required_index(bind, table_name: str, column_name: str) -> bool:
    valid_names = _valid_postgres_index_names(bind)
    return any(
        _is_usable_lookup_index(index, column_name)
        and (
            valid_names is None
            or str(index.get("name") or "") in valid_names
        )
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
    base_name = _index_name(table_name, column_name)
    existing_names = {
        index.get("name")
        for existing_table in inspector.get_table_names()
        for index in inspector.get_indexes(existing_table)
        if index.get("name")
    }
    index_name = base_name
    suffix = 1
    while index_name in existing_names:
        index_name = f"{base_name}_{suffix}"
        suffix += 1

    if bind.dialect.name == "postgresql":
        op.create_index(
            index_name, table_name, [column_name], unique=False,
            postgresql_concurrently=True,
        )
    else:
        op.create_index(index_name, table_name, [column_name], unique=False)
    if not _has_required_index(bind, table_name, column_name):
        raise RuntimeError(f"Index verification failed for {table_name}.{column_name}")
    return index_name


def _is_migration_owned_index(table_name: str, column_name: str, index_name: str) -> bool:
    """Match only names this migration could have created for this exact index."""
    base_name = _index_name(table_name, column_name)
    if index_name == base_name:
        return True
    suffix = index_name.removeprefix(base_name + "_")
    return index_name.startswith(base_name + "_") and suffix.isdigit()


def upgrade() -> None:
    bind = op.get_bind()

    def apply_indexes() -> None:
        for table_name, columns in INDEXES.items():
            for column_name in columns:
                _create_missing_index(bind, table_name, column_name)

    if bind.dialect.name == "postgresql":
        # CREATE INDEX CONCURRENTLY avoids holding write-blocking table locks.
        # Alembic commits before entering this block; if a retry is needed,
        # existing indexes are detected and skipped before the revision is stamped.
        with op.get_context().autocommit_block():
            apply_indexes()
    else:
        apply_indexes()


def downgrade() -> None:
    bind = op.get_bind()
    # A lightweight migration unit test may provide only an inspector-compatible
    # bind. Treat an absent dialect as non-PostgreSQL rather than dereferencing it.
    dialect_name = getattr(getattr(bind, "dialect", None), "name", None)

    def drop_migration_owned_indexes() -> None:
        inspector = inspect(bind)
        for table_name, columns in reversed(tuple(INDEXES.items())):
            if not inspector.has_table(table_name):
                continue
            expected_columns = set(columns)
            for index in inspector.get_indexes(table_name):
                name = str(index.get("name") or "")
                indexed_columns = tuple(index.get("column_names") or ())
                if (
                    len(indexed_columns) == 1
                    and indexed_columns[0] in expected_columns
                    and not bool(index.get("unique", False))
                    and _is_migration_owned_index(table_name, indexed_columns[0], name)
                ):
                    if dialect_name == "postgresql":
                        op.drop_index(name, table_name=table_name, postgresql_concurrently=True)
                    else:
                        op.drop_index(name, table_name=table_name)

    if dialect_name == "postgresql":
        # Keep rollback index removal from blocking ordinary table writes.
        with op.get_context().autocommit_block():
            drop_migration_owned_indexes()
    else:
        drop_migration_owned_indexes()
