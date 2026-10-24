"""Persist complete Delta Engine comparison results.

Revision ID: 20261023_04
Revises: 20261022_03

This migration is additive; existing care records remain unchanged.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "20261023_04"
down_revision = "t2_care_continuity_20261024"
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
        op.create_index("ix_care_delta_runs_person_id", "care_delta_runs", ["person_id"])
        op.create_index("ix_care_delta_runs_person_created", "care_delta_runs", ["person_id", "created_at"])


def downgrade():
    bind = op.get_bind()
    inspector = inspect(bind)
    if inspector.has_table("care_delta_runs"):
        indexes = {item["name"] for item in inspector.get_indexes("care_delta_runs")}
        if "ix_care_delta_runs_person_created" in indexes:
            op.drop_index("ix_care_delta_runs_person_created", table_name="care_delta_runs")
        if "ix_care_delta_runs_person_id" in indexes:
            op.drop_index("ix_care_delta_runs_person_id", table_name="care_delta_runs")
        op.drop_table("care_delta_runs")
