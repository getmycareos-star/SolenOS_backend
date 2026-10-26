"""Persist Qwen outputs separately from job status and authoritative care facts.

Revision ID: 20261026_04_qwen_output_safety
Revises: 20261026_03_identity_security
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

from app.core.database import Base

revision = "20261026_04_qwen_output_safety"
down_revision = "20261026_03_identity_security"
branch_labels = None
depends_on = None

SAFETY_TABLES = (
    "safety_benchmark_cases",
    "safety_regressions",
    "safety_events",
    "safety_feedback",
    "safety_decisions",
    "safety_audit_records",
    "safety_blind_spots",
    "escalations",
    "safety_policy_rules",
)


def upgrade():
    bind = op.get_bind()
    inspector = inspect(bind)

    # Repair missing safety tables from existing ORM definitions, without
    # recreating or dropping any existing table or record.
    missing_safety = [
        Base.metadata.tables[name]
        for name in SAFETY_TABLES
        if name in Base.metadata.tables and not inspector.has_table(name)
    ]
    if missing_safety:
        Base.metadata.create_all(bind=bind, tables=missing_safety, checkfirst=True)

    # Structured dimensions are policy/assessment metadata, not care facts.
    if not inspect(bind).has_table("assessment_dimensions"):
        op.create_table(
            "assessment_dimensions",
            sa.Column("id", sa.String(36), primary_key=True, nullable=False),
            sa.Column("dimension_key", sa.String(80), nullable=False, unique=True),
            sa.Column("display_name", sa.String(160), nullable=False),
            sa.Column("description", sa.Text(), nullable=True),
            sa.Column("applies_to", sa.JSON(), nullable=False, server_default=sa.text("'[]'")),
            sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.Column("version", sa.String(40), nullable=False, server_default="1"),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        )
        op.create_index("ix_assessment_dimensions_active", "assessment_dimensions", ["is_active"])

    # Generated output, job history, and safety review remain separate from LCR.
    qwen_tables = [
        Base.metadata.tables[name]
        for name in ("qwen_model_outputs", "qwen_job_events", "qwen_safety_evaluations")
        if name in Base.metadata.tables and not inspect(bind).has_table(name)
    ]
    if qwen_tables:
        Base.metadata.create_all(bind=bind, tables=qwen_tables, checkfirst=True)


def downgrade():
    # Retain safety/audit/output history; destructive downgrade is intentionally blocked.
    raise RuntimeError("Qwen output/safety history is retained; use a reviewed backup and data-retention plan.")
