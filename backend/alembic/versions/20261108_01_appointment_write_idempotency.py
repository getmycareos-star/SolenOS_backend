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
    constraints = {item.get("name") for item in inspect(bind).get_unique_constraints("appointment_workspace_records")}
    if "uq_appointment_workspace_person_idempotency" not in indexes | constraints:
        op.create_index(
            "uq_appointment_workspace_person_idempotency",
            "appointment_workspace_records",
            ["person_id", "idempotency_key"],
            unique=True,
        )

    indexes = {index["name"] for index in inspect(bind).get_indexes("appointment_workspace_revisions")}
    constraints = {item.get("name") for item in inspect(bind).get_unique_constraints("appointment_workspace_revisions")}
    if "uq_appointment_workspace_revision_idempotency" not in indexes | constraints:
        op.create_index(
            "uq_appointment_workspace_revision_idempotency",
            "appointment_workspace_revisions",
            ["record_id", "idempotency_key"],
            unique=True,
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    for table, constraint_name in (
        ("appointment_workspace_revisions", "uq_appointment_workspace_revision_idempotency"),
        ("appointment_workspace_records", "uq_appointment_workspace_person_idempotency"),
    ):
        if not inspector.has_table(table):
            continue
        columns = {column["name"] for column in inspect(bind).get_columns(table)}
        if "idempotency_key" in columns:
            used = bind.execute(
                sa.text(f'SELECT COUNT(*) FROM "{table}" WHERE idempotency_key IS NOT NULL')
            ).scalar_one()
            if used:
                raise RuntimeError(
                    f"Refusing to remove {table} idempotency keys while persisted values are in use."
                )
        unique_constraints = {
            item.get("name") for item in inspect(bind).get_unique_constraints(table)
        }
        indexes = {item.get("name") for item in inspect(bind).get_indexes(table)}
        if constraint_name in unique_constraints:
            op.drop_constraint(constraint_name, table, type_="unique")
        elif constraint_name in indexes:
            op.drop_index(constraint_name, table_name=table)
        columns = {column["name"] for column in inspect(bind).get_columns(table)}
        for name in ("idempotency_request_sha256", "idempotency_key"):
            if name in columns:
                op.drop_column(table, name)
