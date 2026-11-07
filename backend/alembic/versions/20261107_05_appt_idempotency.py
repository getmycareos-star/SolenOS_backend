"""Make appointment workspace writes retry-safe.

Revision ID: 20261107_05_appt_idempotency
Revises: 20261107_04_assert_event_time
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "20261107_05_appt_idempotency"
down_revision = "20261107_04_assert_event_time"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)

    if not inspector.has_table("appointment_workspace_records"):
        raise RuntimeError("appointment_workspace_records is required before adding idempotency")
    record_columns = {column["name"] for column in inspect(bind).get_columns("appointment_workspace_records")}
    if "idempotency_key" not in record_columns:
        op.add_column("appointment_workspace_records", sa.Column("idempotency_key", sa.String(128), nullable=True))
    if "idempotency_request_sha256" not in record_columns:
        op.add_column("appointment_workspace_records", sa.Column("idempotency_request_sha256", sa.String(64), nullable=True))

    if not inspect(bind).has_table("appointment_workspace_revisions"):
        raise RuntimeError("appointment_workspace_revisions is required before adding idempotency")
    revision_columns = {column["name"] for column in inspect(bind).get_columns("appointment_workspace_revisions")}
    if "idempotency_key" not in revision_columns:
        op.add_column("appointment_workspace_revisions", sa.Column("idempotency_key", sa.String(128), nullable=True))
    if "idempotency_request_sha256" not in revision_columns:
        op.add_column("appointment_workspace_revisions", sa.Column("idempotency_request_sha256", sa.String(64), nullable=True))

    indexes = {item["name"] for item in inspect(bind).get_indexes("appointment_workspace_records")}
    constraints = {item.get("name") for item in inspect(bind).get_unique_constraints("appointment_workspace_records")}
    if "uq_appointment_workspace_person_caregiver_idempotency" not in indexes | constraints:
        op.create_index(
            "uq_appointment_workspace_person_caregiver_idempotency",
            "appointment_workspace_records",
            ["person_id", "created_by_caregiver_id", "idempotency_key"],
            unique=True,
        )

    indexes = {item["name"] for item in inspect(bind).get_indexes("appointment_workspace_revisions")}
    constraints = {item.get("name") for item in inspect(bind).get_unique_constraints("appointment_workspace_revisions")}
    if "uq_appointment_workspace_revision_caregiver_idempotency" not in indexes | constraints:
        op.create_index(
            "uq_appointment_workspace_revision_caregiver_idempotency",
            "appointment_workspace_revisions",
            ["record_id", "changed_by_caregiver_id", "idempotency_key"],
            unique=True,
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    for table, index_name in (
        ("appointment_workspace_revisions", "uq_appointment_workspace_revision_caregiver_idempotency"),
        ("appointment_workspace_records", "uq_appointment_workspace_person_caregiver_idempotency"),
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
        indexes = {item.get("name") for item in inspect(bind).get_indexes(table)}
        constraints = {item.get("name") for item in inspect(bind).get_unique_constraints(table)}
        # PostgreSQL backs a UNIQUE constraint with an index. Drop the
        # constraint first when inspection reports both objects; DROP INDEX
        # cannot remove an index owned by a table constraint.
        if index_name in constraints:
            op.drop_constraint(index_name, table, type_="unique")
        elif index_name in indexes:
            op.drop_index(index_name, table_name=table)
        columns = {column["name"] for column in inspect(bind).get_columns(table)}
        for column_name in ("idempotency_request_sha256", "idempotency_key"):
            if column_name in columns:
                op.drop_column(table, column_name)
