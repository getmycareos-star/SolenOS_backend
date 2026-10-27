"""Add durable family care handoffs and immutable handoff event history.

Revision ID: t3_care_handoffs_20261023
Revises: t2_auth_reconcile_20261023
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "t3_care_handoffs_20261023"
down_revision = "t2_auth_reconcile_20261023"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    inspector = inspect(bind)
    if not inspector.has_table("care_handoffs"):
        op.create_table(
            "care_handoffs",
            sa.Column("id", sa.String(), primary_key=True),
            sa.Column("person_id", sa.String(), sa.ForeignKey("persons.id"), nullable=False),
            sa.Column("created_by_caregiver_id", sa.String(), sa.ForeignKey("caregivers.id"), nullable=False),
            sa.Column("assigned_to_caregiver_id", sa.String(), sa.ForeignKey("caregivers.id"), nullable=True),
            sa.Column("title", sa.String(), nullable=False),
            sa.Column("summary", sa.Text(), nullable=False),
            sa.Column("outstanding_items", sa.JSON(), nullable=False),
            sa.Column("recent_changes", sa.JSON(), nullable=False),
            sa.Column("safety_concerns", sa.JSON(), nullable=False),
            sa.Column("status", sa.String(), nullable=False, server_default="open"),
            sa.Column("acknowledged_by_caregiver_id", sa.String(), sa.ForeignKey("caregivers.id"), nullable=True),
            sa.Column("acknowledged_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("closed_by_caregiver_id", sa.String(), sa.ForeignKey("caregivers.id"), nullable=True),
            sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        )
    inspector = inspect(bind)
    if not inspector.has_table("care_handoff_events"):
        op.create_table(
            "care_handoff_events",
            sa.Column("id", sa.String(), primary_key=True),
            sa.Column("handoff_id", sa.String(), sa.ForeignKey("care_handoffs.id"), nullable=False),
            sa.Column("person_id", sa.String(), sa.ForeignKey("persons.id"), nullable=False),
            sa.Column("actor_caregiver_id", sa.String(), sa.ForeignKey("caregivers.id"), nullable=False),
            sa.Column("event_type", sa.String(), nullable=False),
            sa.Column("details", sa.JSON(), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        )
    indexes = {item["name"] for item in inspect(bind).get_indexes("care_handoffs")}
    if "ix_care_handoffs_person_id" not in indexes:
        op.create_index("ix_care_handoffs_person_id", "care_handoffs", ["person_id"], unique=False)
    if "ix_care_handoffs_assigned_to_caregiver_id" not in indexes:
        op.create_index("ix_care_handoffs_assigned_to_caregiver_id", "care_handoffs", ["assigned_to_caregiver_id"], unique=False)
    if "ix_care_handoffs_status" not in indexes:
        op.create_index("ix_care_handoffs_status", "care_handoffs", ["status"], unique=False)
    if "ix_care_handoffs_person_status_created" not in indexes:
        op.create_index("ix_care_handoffs_person_status_created", "care_handoffs", ["person_id", "status", "created_at"], unique=False)
    event_indexes = {item["name"] for item in inspect(bind).get_indexes("care_handoff_events")}
    if "ix_care_handoff_events_handoff_id" not in event_indexes:
        op.create_index("ix_care_handoff_events_handoff_id", "care_handoff_events", ["handoff_id"], unique=False)
    if "ix_care_handoff_events_person_id" not in event_indexes:
        op.create_index("ix_care_handoff_events_person_id", "care_handoff_events", ["person_id"], unique=False)
    if "ix_care_handoff_events_handoff_created" not in event_indexes:
        op.create_index("ix_care_handoff_events_handoff_created", "care_handoff_events", ["handoff_id", "created_at"], unique=False)


def downgrade():
    bind = op.get_bind()
    inspector = inspect(bind)
    if inspector.has_table("care_handoff_events"):
        for index in inspect(bind).get_indexes("care_handoff_events"):
            op.drop_index(index["name"], table_name="care_handoff_events")
        op.drop_table("care_handoff_events")
    inspector = inspect(bind)
    if inspector.has_table("care_handoffs"):
        for index in inspect(bind).get_indexes("care_handoffs"):
            op.drop_index(index["name"], table_name="care_handoffs")
        op.drop_table("care_handoffs")
