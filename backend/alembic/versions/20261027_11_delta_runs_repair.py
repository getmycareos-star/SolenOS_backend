"""Repair missing Delta run table on databases already stamped past continuity migration.

Revision ID: 20261027_11_delta_runs_repair
Revises: 20261027_10_appointment_workspace
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "20261027_11_delta_runs_repair"
down_revision = "20261027_10_appointment_workspace"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    inspector = inspect(bind)
    if not inspector.has_table("care_delta_runs"):
        op.create_table(
            "care_delta_runs",
            sa.Column("id", sa.String(), primary_key=True, nullable=False),
            sa.Column("person_id", sa.String(), sa.ForeignKey("persons.id"), nullable=False),
            sa.Column("source", sa.String(), nullable=False),
            sa.Column("event_date", sa.DateTime(timezone=True), nullable=False),
            sa.Column("source_event_id", sa.String(), sa.ForeignKey("care_events.id"), nullable=True),
            sa.Column("evidence_ids", sa.JSON(), nullable=False),
            sa.Column("delta_json", sa.Text(), nullable=False),
            sa.Column("model_name", sa.String(), nullable=False),
            sa.Column("created_by_caregiver_id", sa.String(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        )
    inspector = inspect(bind)
    indexes = {item["name"] for item in inspector.get_indexes("care_delta_runs")}
    if "ix_care_delta_runs_person_id" not in indexes:
        op.create_index("ix_care_delta_runs_person_id", "care_delta_runs", ["person_id"])
    if "ix_care_delta_runs_person_created" not in indexes:
        op.create_index("ix_care_delta_runs_person_created", "care_delta_runs", ["person_id", "created_at"])


def downgrade():
    # Keep continuity data; repair is intentionally non-destructive.
    pass
