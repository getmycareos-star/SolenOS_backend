"""Separate legacy account-security attempts from care notification attempts.

Revision ID: 20261120_07_notification_delivery_reconcile
Revises: 20261120_06_caregiver_person_index
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "20261120_07_notification_delivery_reconcile"
down_revision = "20261120_06_caregiver_person_index"
branch_labels = None
depends_on = None


def _table_exists(bind, name):
    return inspect(bind).has_table(name)


def _ensure_index(bind, table_name, column_name):
    inspector = inspect(bind)
    if not inspector.has_table(table_name):
        raise RuntimeError(f"Cannot add {table_name}.{column_name} index: table is missing")
    columns = {column["name"] for column in inspector.get_columns(table_name)}
    if column_name not in columns:
        raise RuntimeError(f"Cannot add {table_name}.{column_name} index: column is missing")
    index_name = f"ix_{table_name}_{column_name}"
    op.execute(sa.text(
        f"CREATE INDEX IF NOT EXISTS {index_name} ON {table_name} ({column_name})"
    ))


def upgrade():
    bind = op.get_bind()
    inspector = inspect(bind)
    live_name = "notification_delivery_attempts"
    legacy_name = "account_security_notification_delivery_attempts"

    # The identity-security migration originally used this table for attempts
    # against account_security_notifications. The care-delivery worker later
    # reused the same name for a different record shape. Preserve every old row
    # and its FK intact before creating the care-notification table.
    if inspector.has_table(live_name):
        columns = {column["name"] for column in inspector.get_columns(live_name)}
        foreign_keys = inspector.get_foreign_keys(live_name)
        legacy_shape = (
            "status" in columns
            or "failure_reason" in columns
            or any(item.get("referred_table") == "account_security_notifications" for item in foreign_keys)
        )
        if legacy_shape:
            if inspector.has_table(legacy_name):
                raise RuntimeError(
                    "Both legacy and active account-security delivery-attempt tables exist; "
                    "refusing to overwrite either table."
                )
            op.rename_table(live_name, legacy_name)

    op.execute(sa.text("""
        CREATE TABLE IF NOT EXISTS notification_delivery_attempts (
            id VARCHAR(36) NOT NULL PRIMARY KEY,
            actor_id VARCHAR(255),
            subject_id VARCHAR(255),
            person_id VARCHAR(255),
            caregiver_id VARCHAR(255),
            notification_id VARCHAR(255) NOT NULL,
            attempt_number INTEGER NOT NULL,
            channel TEXT NOT NULL,
            attempted_at TIMESTAMP WITH TIME ZONE NOT NULL,
            result TEXT NOT NULL,
            failure_code TEXT,
            provider_reference TEXT,
            request_id VARCHAR(255),
            correlation_id VARCHAR(255),
            policy_version VARCHAR(100),
            metadata JSON NOT NULL,
            created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
            CONSTRAINT fk_notification_delivery_attempts_notification
                FOREIGN KEY (notification_id)
                REFERENCES care_notifications(id)
                ON DELETE CASCADE
        )
    """))

    inspector = inspect(bind)
    attempt_columns = {column["name"]: column for column in inspector.get_columns(live_name)}
    required_columns = {
        "id", "actor_id", "subject_id", "person_id", "caregiver_id",
        "notification_id", "attempt_number", "channel", "attempted_at",
        "result", "failure_code", "provider_reference", "request_id",
        "correlation_id", "policy_version", "metadata", "created_at", "updated_at",
    }
    missing = sorted(required_columns - set(attempt_columns))
    if missing:
        raise RuntimeError(
            "Care notification delivery-attempt table is incomplete: " + ", ".join(missing)
        )
    required_non_null = (
        "id", "notification_id", "attempt_number", "channel", "attempted_at",
        "result", "metadata", "created_at", "updated_at",
    )
    nullable = [name for name in required_non_null if attempt_columns[name].get("nullable", True)]
    if nullable:
        raise RuntimeError(
            "Care notification delivery-attempt columns must be non-null: " + ", ".join(nullable)
        )
    attempt_foreign_keys = inspector.get_foreign_keys(live_name)
    if not any(
        tuple(item.get("constrained_columns") or ()) == ("notification_id",)
        and item.get("referred_table") == "care_notifications"
        for item in attempt_foreign_keys
    ):
        if bind.dialect.name == "sqlite":
            raise RuntimeError("SQLite care notification delivery-attempt table is missing its notification foreign key")
        op.create_foreign_key(
            "fk_notification_delivery_attempts_notification",
            live_name,
            "care_notifications",
            ["notification_id"],
            ["id"],
            ondelete="CASCADE",
        )

    for column in (
        "actor_id", "subject_id", "person_id", "caregiver_id",
        "notification_id", "attempted_at", "request_id", "correlation_id",
    ):
        _ensure_index(bind, live_name, column)

    if _table_exists(bind, "care_notifications"):
        notification_columns = {
            column["name"]: column for column in inspect(bind).get_columns("care_notifications")
        }
        if "updated_at" in notification_columns:
            op.execute(sa.text(
                "UPDATE care_notifications "
                "SET updated_at = COALESCE(updated_at, created_at, CURRENT_TIMESTAMP) "
                "WHERE updated_at IS NULL"
            ))
            if notification_columns["updated_at"].get("nullable", True):
                with op.batch_alter_table("care_notifications") as batch:
                    batch.alter_column(
                        "updated_at",
                        existing_type=sa.DateTime(timezone=True),
                        nullable=False,
                    )

    if _table_exists(bind, "data_subject_requests"):
        request_columns = {
            column["name"]: column for column in inspect(bind).get_columns("data_subject_requests")
        }
        if "subject_id" in request_columns:
            # Older active requests may have stored only person_id. Backfill
            # those rows, but preserve NULL for fulfilled erasure receipts:
            # privacy completion intentionally clears every direct subject
            # identifier so the request itself cannot re-identify the person.
            op.execute(sa.text(
                "UPDATE data_subject_requests "
                "SET subject_id = person_id "
                "WHERE subject_id IS NULL AND person_id IS NOT NULL AND request_status <> 'fulfilled'"
            ))
            unresolved_active = bind.execute(sa.text(
                "SELECT COUNT(*) FROM data_subject_requests "
                "WHERE subject_id IS NULL AND request_status <> 'fulfilled'"
            )).scalar_one()
            if unresolved_active:
                raise RuntimeError(
                    "Cannot reconcile active data-subject requests without a subject identifier: "
                    + str(unresolved_active)
                )

    expected_indexes = (
        ("delivery_failure_events", "delivery_id"),
        ("delivery_failure_events", "recipient_id"),
        ("delivery_failure_events", "resolved_at"),
        ("care_assertions", "source_conversation_id"),
        ("evidence_claim_assessments", "claim_id"),
        ("evidence_claim_assessments", "person_id"),
        ("evidence_claim_dependencies", "person_id"),
        ("evidence_source_relationships", "person_id"),
        ("evidence_observations", "evidence_id"),
        ("evidence_observations", "person_id"),
        ("evidence_completeness_policies", "person_id"),
        ("evidence_completeness_assessments", "person_id"),
        ("handoff_records", "expires_at"),
        ("handoff_recipients", "handoff_id"),
        ("handoff_recipients", "recipient_id"),
        ("handoff_acknowledgements", "handoff_id"),
        ("handoff_acknowledgements", "recipient_id"),
        ("handoff_access_logs", "handoff_id"),
        ("handoff_access_logs", "recipient_id"),
        ("notification_read_receipts", "notification_id"),
        ("notification_read_receipts", "reader_id"),
        ("data_subject_requests", "requested_at"),
        ("data_subject_requests", "requester_id"),
        ("appointment_workspace_records", "person_id"),
        ("appointment_workspace_revisions", "person_id"),
        ("appointment_workspace_revisions", "record_id"),
        ("ai_output_lcr_proposals", "source_message_id"),
    )
    for table_name, column_name in expected_indexes:
        _ensure_index(bind, table_name, column_name)


def downgrade():
    bind = op.get_bind()
    inspector = inspect(bind)
    live_name = "notification_delivery_attempts"
    legacy_name = "account_security_notification_delivery_attempts"
    if inspector.has_table(live_name):
        care_legacy_name = "care_notification_delivery_attempts"
        if inspector.has_table(care_legacy_name):
            raise RuntimeError(
                "Cannot downgrade: care notification delivery-attempt archive already exists."
            )
        op.rename_table(live_name, care_legacy_name)
    if inspect(bind).has_table(legacy_name):
        if inspect(bind).has_table(live_name):
            raise RuntimeError(
                "Cannot restore account-security delivery-attempt table because its original name is occupied."
            )
        op.rename_table(legacy_name, live_name)
