"""Persist each reminder delivery attempt.

Revision ID: t2_delivery_attempts_20261023
Revises: t2_med_auth_20261023
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "t2_delivery_attempts_20261023"
down_revision = "t2_med_auth_20261023"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    inspector = inspect(bind)
    if not inspector.has_table("reminder_delivery_attempts"):
        op.create_table(
            "reminder_delivery_attempts",
            sa.Column("id", sa.String(), primary_key=True),
            sa.Column("delivery_id", sa.String(), sa.ForeignKey("reminder_deliveries.id"), nullable=False),
            sa.Column("attempt_number", sa.Integer(), nullable=False),
            sa.Column("channel", sa.String(), nullable=False, server_default="in_app"),
            sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("outcome", sa.String(), nullable=False, server_default="pending"),
            sa.Column("error_type", sa.String(), nullable=True),
            sa.Column("idempotency_key", sa.String(), nullable=False, unique=True),
            sa.UniqueConstraint("delivery_id", "attempt_number", name="uq_reminder_attempt_number"),
        )
    indexes = {item["name"] for item in inspect(bind).get_indexes("reminder_delivery_attempts")}
    if "ix_reminder_delivery_attempts_delivery_id" not in indexes:
        op.create_index("ix_reminder_delivery_attempts_delivery_id", "reminder_delivery_attempts", ["delivery_id"], unique=False)


def downgrade():
    bind = op.get_bind()
    inspector = inspect(bind)
    if inspector.has_table("reminder_delivery_attempts"):
        for index in list(inspect(bind).get_indexes("reminder_delivery_attempts")):
            op.drop_index(index["name"], table_name="reminder_delivery_attempts")
        op.drop_table("reminder_delivery_attempts")
