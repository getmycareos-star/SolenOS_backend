"""Widen notification attempt references for opaque notification identifiers.

Revision ID: 20261121_04_notification_attempt_id_width
Revises: 20261121_03_open_question_review_receipt

CareNotification identifiers are opaque strings, not guaranteed UUIDs. The
attempt ledger must preserve the full referenced identifier without truncation.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "20261121_04_notification_attempt_id_width"
down_revision = "20261121_03_open_question_review_receipt"
branch_labels = None
depends_on = None

TABLE = "notification_delivery_attempts"
COLUMN = "notification_id"
TARGET_LENGTH = 255


def upgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    if not inspector.has_table(TABLE):
        raise RuntimeError(f"Cannot widen {TABLE}.{COLUMN}: table is missing")

    columns = {item["name"]: item for item in inspector.get_columns(TABLE)}
    if COLUMN not in columns:
        raise RuntimeError(f"Cannot widen {TABLE}.{COLUMN}: column is missing")

    current_type = columns[COLUMN]["type"]
    current_length = getattr(current_type, "length", None)
    if current_length is not None and current_length >= TARGET_LENGTH:
        return

    if bind.dialect.name == "sqlite":
        with op.batch_alter_table(TABLE) as batch:
            batch.alter_column(
                COLUMN,
                existing_type=current_type,
                type_=sa.String(length=TARGET_LENGTH),
                existing_nullable=False,
            )
    else:
        op.alter_column(
            TABLE,
            COLUMN,
            existing_type=current_type,
            type_=sa.String(length=TARGET_LENGTH),
            existing_nullable=False,
        )

    refreshed = {
        item["name"]: item for item in inspect(bind).get_columns(TABLE)
    }
    actual_length = getattr(refreshed[COLUMN]["type"], "length", None)
    if actual_length is not None and actual_length < TARGET_LENGTH:
        raise RuntimeError(
            f"{TABLE}.{COLUMN} remains limited to {actual_length} characters"
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    if not inspector.has_table(TABLE):
        return
    columns = {item["name"]: item for item in inspector.get_columns(TABLE)}
    if COLUMN not in columns:
        return

    oversized = bind.execute(
        sa.text(
            f"SELECT COUNT(*) FROM {TABLE} "
            f"WHERE length({COLUMN}) > 36"
        )
    ).scalar_one()
    if oversized:
        raise RuntimeError(
            f"Cannot narrow {TABLE}.{COLUMN}: {oversized} durable references exceed 36 characters"
        )

    current_type = columns[COLUMN]["type"]
    if getattr(current_type, "length", None) == 36:
        return
    if bind.dialect.name == "sqlite":
        with op.batch_alter_table(TABLE) as batch:
            batch.alter_column(
                COLUMN,
                existing_type=current_type,
                type_=sa.String(length=36),
                existing_nullable=False,
            )
    else:
        op.alter_column(
            TABLE,
            COLUMN,
            existing_type=current_type,
            type_=sa.String(length=36),
            existing_nullable=False,
        )
