"""Allow redacted privacy requests to retain null subject identifiers.

Revision ID: 20261120_08_allow_redacted_subject_id
Revises: 20261120_07_notification_delivery_reconcile
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "20261120_08_allow_redacted_subject_id"
down_revision = "20261120_07_notification_delivery_reconcile"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    table_name = "data_subject_requests"
    if not inspector.has_table(table_name):
        return
    columns = {column["name"]: column for column in inspector.get_columns(table_name)}
    subject = columns.get("subject_id")
    if subject is not None and not subject.get("nullable", True):
        with op.batch_alter_table(table_name) as batch:
            batch.alter_column(
                "subject_id",
                existing_type=sa.String(length=255),
                nullable=True,
            )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    table_name = "data_subject_requests"
    if not inspector.has_table(table_name):
        return
    columns = {column["name"]: column for column in inspector.get_columns(table_name)}
    subject = columns.get("subject_id")
    if subject is None or not subject.get("nullable", True):
        return
    remaining_nulls = bind.execute(
        sa.text("SELECT COUNT(*) FROM data_subject_requests WHERE subject_id IS NULL")
    ).scalar_one()
    if remaining_nulls:
        raise RuntimeError(
            "Cannot restore NOT NULL subject_id while redacted privacy requests contain null identifiers."
        )
    with op.batch_alter_table(table_name) as batch:
        batch.alter_column(
            "subject_id",
            existing_type=sa.String(length=255),
            nullable=False,
        )
