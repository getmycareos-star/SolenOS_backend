"""Make appointment workspace create and revision writes idempotent.

Revision ID: 20261108_01_appt_idempotency
Revises: 20261107_02_handoff_drafts
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "20261108_01_appt_idempotency"
down_revision = "20261107_02_handoff_drafts"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)

    record_columns = {column["name"] for column in inspector.get_columns("appointment_workspace_records")}
    if "idempotency_key" not in record_columns:
        op.add_column("appointment_workspace_records", sa.Column("idempotency_key", sa.String(128), nullable=True))
    if "idempotency_request_sha256" not in record_columns:
        op.add_column("appointment_workspace_records", sa.Column("idempotency_request_sha256", sa.String(64), nullable=True))

    revision_columns = {column["name"] for column in inspect(bind).get_columns("appointment_workspace_revisions")}
    if "idempotency_key" not in revision_columns:
        op.add_column("appointment_workspace_revisions", sa.Column("idempotency_key", sa.String(128), nullable=True))
    if "idempotency_request_sha256" not in revision_columns:
        op.add_column("appointment_workspace_revisions", sa.Column("idempotency_request_sha256", sa.String(64), nullable=True))

    indexes = {index["name"] for index in inspect(bind).get_indexes("appointment_workspace_records")}
    if "uq_appointment_workspace_person_idempotency" not in indexes:
        op.create_index(
            "uq_appointment_workspace_person_idempotency",
            "appointment_workspace_records",
            ["person_id", "idempotency_key"],
            unique=True,
        )

    indexes = {index["name"] for index in inspect(bind).get_indexes("appointment_workspace_revisions")}
    if "uq_appointment_workspace_revision_idempotency" not in indexes:
        op.create_index(
            "uq_appointment_workspace_revision_idempotency",
            "appointment_workspace_revisions",
            ["record_id", "idempotency_key"],
            unique=True,
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    if inspector.has_table("appointment_workspace_revisions"):
        indexes = {index["name"] for index in inspector.get_indexes("appointment_workspace_revisions")}
        if "uq_appointment_workspace_revision_idempotency" in indexes:
            op.drop_index("uq_appointment_workspace_revision_idempotency", table_name="appointment_workspace_revisions")
        columns = {column["name"] for column in inspect(bind).get_columns("appointment_workspace_revisions")}
        for name in ("idempotency_request_sha256", "idempotency_key"):
            if name in columns:
                op.drop_column("appointment_workspace_revisions", name)
    if inspector.has_table("appointment_workspace_records"):
        indexes = {index["name"] for index in inspect(bind).get_indexes("appointment_workspace_records")}
        if "uq_appointment_workspace_person_idempotency" in indexes:
            op.drop_index("uq_appointment_workspace_person_idempotency", table_name="appointment_workspace_records")
        columns = {column["name"] for column in inspect(bind).get_columns("appointment_workspace_records")}
        for name in ("idempotency_request_sha256", "idempotency_key"):
            if name in columns:
                op.drop_column("appointment_workspace_records", name)
