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

    # Structured assessment dimensions use their canonical ORM definition.
    if "assessment_dimensions" in Base.metadata.tables and not inspect(bind).has_table("assessment_dimensions"):
        Base.metadata.create_all(
            bind=bind,
            tables=[Base.metadata.tables["assessment_dimensions"]],
            checkfirst=True,
        )

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
