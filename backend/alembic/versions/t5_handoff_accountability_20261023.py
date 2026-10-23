"""Add deadlines, recipient access scope, and completion reporting to handoffs.

Revision ID: t5_handoff_accountability_20261023
Revises: t4_document_assets_20261023
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "t5_handoff_accountability_20261023"
down_revision = "t4_document_assets_20261023"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    inspector = inspect(bind)
    if not inspector.has_table("care_handoffs"):
        return
    existing = {column["name"] for column in inspector.get_columns("care_handoffs")}
    additions = [
        ("access_scope", sa.String(), False, "care_circle"),
        ("due_at", sa.DateTime(timezone=True), True, None),
        ("reminder_at", sa.DateTime(timezone=True), True, None),
        ("completion_report", sa.Text(), True, None),
        ("completed_by_caregiver_id", sa.String(), True, None),
        ("completed_at", sa.DateTime(timezone=True), True, None),
    ]
    for name, kind, nullable, default in additions:
        if name not in existing:
            kwargs = {"nullable": nullable}
            if default is not None:
                kwargs["server_default"] = default
            if name == "completed_by_caregiver_id":
                op.add_column("care_handoffs", sa.Column(name, kind, sa.ForeignKey("caregivers.id"), **kwargs))
            else:
                op.add_column("care_handoffs", sa.Column(name, kind, **kwargs))
    indexes = {item["name"] for item in inspect(bind).get_indexes("care_handoffs")}
    if "ix_care_handoffs_due_at" not in indexes:
        op.create_index("ix_care_handoffs_due_at", "care_handoffs", ["person_id", "due_at"], unique=False)

    reminder_columns = {column["name"]: column for column in inspect(bind).get_columns("care_reminders")} if inspect(bind).has_table("care_reminders") else {}
    if "action_id" in reminder_columns and reminder_columns["action_id"]["nullable"] is False:
        op.alter_column("care_reminders", "action_id", existing_type=sa.String(), nullable=True)
    if inspect(bind).has_table("care_reminders"):
        existing_reminder_columns = {column["name"] for column in inspect(bind).get_columns("care_reminders")}
        if "handoff_id" not in existing_reminder_columns:
            op.add_column("care_reminders", sa.Column("handoff_id", sa.String(), sa.ForeignKey("care_handoffs.id"), nullable=True))
        if "recipient_caregiver_id" not in existing_reminder_columns:
            op.add_column("care_reminders", sa.Column("recipient_caregiver_id", sa.String(), sa.ForeignKey("caregivers.id"), nullable=True))
        reminder_indexes = {item["name"] for item in inspect(bind).get_indexes("care_reminders")}
        if "ix_care_reminders_handoff_id" not in reminder_indexes:
            op.create_index("ix_care_reminders_handoff_id", "care_reminders", ["handoff_id"], unique=True)
        if "ix_care_reminders_recipient_caregiver_id" not in reminder_indexes:
            op.create_index("ix_care_reminders_recipient_caregiver_id", "care_reminders", ["recipient_caregiver_id"], unique=False)


def downgrade():
    bind = op.get_bind()
    inspector = inspect(bind)
    if not inspector.has_table("care_handoffs"):
        return
    indexes = {item["name"] for item in inspect(bind).get_indexes("care_handoffs")}
    if "ix_care_handoffs_due_at" in indexes:
        op.drop_index("ix_care_handoffs_due_at", table_name="care_handoffs")
    existing = {column["name"] for column in inspect(bind).get_columns("care_handoffs")}
    if inspector.has_table("care_reminders"):
        reminder_indexes = {item["name"] for item in inspect(bind).get_indexes("care_reminders")}
        for index_name in ("ix_care_reminders_handoff_id", "ix_care_reminders_recipient_caregiver_id"):
            if index_name in reminder_indexes:
                op.drop_index(index_name, table_name="care_reminders")
        reminder_columns = {column["name"] for column in inspect(bind).get_columns("care_reminders")}
        for name in ("recipient_caregiver_id", "handoff_id"):
            if name in reminder_columns:
                op.drop_column("care_reminders", name)
        if "action_id" in reminder_columns:
            op.alter_column("care_reminders", "action_id", existing_type=sa.String(), nullable=False)
    for name in ("completed_at", "completed_by_caregiver_id", "completion_report", "reminder_at", "due_at", "access_scope"):
        if name in existing:
            op.drop_column("care_handoffs", name)
