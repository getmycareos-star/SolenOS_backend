"""Allow an appointment to be recorded before its date is known.

Revision ID: 20261121_01_appointment_date_optional
Revises: 20261120_10_caregiver_context_model_alignment

An unknown appointment date is a valid information state. This migration
relaxes only appointments.scheduled_at; it does not create or infer dates.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "20261121_01_appt_date_optional"
down_revision = "20261120_10_caregiver_context_model_alignment"
branch_labels = None
depends_on = None

TABLE = "appointments"
COLUMN = "scheduled_at"


def upgrade():
    bind = op.get_bind()
    inspector = inspect(bind)
    if not inspector.has_table(TABLE):
        raise RuntimeError("Cannot allow unknown appointment dates: appointments table is missing")
    columns = {item["name"]: item for item in inspector.get_columns(TABLE)}
    column = columns.get(COLUMN)
    if column is None:
        raise RuntimeError("Cannot allow unknown appointment dates: appointments.scheduled_at is missing")
    if not bool(column.get("nullable", True)):
        if bind.dialect.name == "sqlite":
            with op.batch_alter_table(TABLE) as batch:
                batch.alter_column(
                    COLUMN,
                    existing_type=column["type"],
                    nullable=True,
                )
        else:
            op.alter_column(
                TABLE,
                COLUMN,
                existing_type=column["type"],
                nullable=True,
            )
    refreshed = {item["name"]: item for item in inspect(bind).get_columns(TABLE)}
    if not bool(refreshed[COLUMN].get("nullable", True)):
        raise RuntimeError("appointments.scheduled_at remains NOT NULL after migration")


def downgrade():
    bind = op.get_bind()
    inspector = inspect(bind)
    if not inspector.has_table(TABLE):
        return
    columns = {item["name"]: item for item in inspector.get_columns(TABLE)}
    column = columns.get(COLUMN)
    if column is None or not bool(column.get("nullable", True)):
        return
    unknown_dates = bind.execute(
        sa.text("SELECT COUNT(*) FROM appointments WHERE scheduled_at IS NULL")
    ).scalar_one()
    if unknown_dates:
        raise RuntimeError(
            "Cannot restore NOT NULL appointments.scheduled_at while "
            f"{unknown_dates} appointment records have an unknown date."
        )
    if bind.dialect.name == "sqlite":
        with op.batch_alter_table(TABLE) as batch:
            batch.alter_column(COLUMN, existing_type=column["type"], nullable=False)
    else:
        op.alter_column(TABLE, COLUMN, existing_type=column["type"], nullable=False)
