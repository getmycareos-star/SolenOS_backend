"""Add generic AI output governance and LCR proposal decision history.

Revision ID: 20261027_06_ai_output_governance
Revises: 20261027_05_qwen_output_safety
"""
from alembic import op
from sqlalchemy import inspect

from app.core.database import Base
# Register referenced parent tables and all table definitions before create_all.
from app.models import care as _care  # noqa: F401
from app.models import ai_output_governance as _ai_output_governance  # noqa: F401
from app.models import qwen_background_job as _qwen_background_job  # noqa: F401
from app.models import safety as _safety  # noqa: F401

revision = "20261027_06_ai_output_governance"
down_revision = "20261027_05_qwen_output_safety"
branch_labels = None
depends_on = None

TABLES = (
    "ai_output_rejections",
    "ai_safety_checks",
    "ai_safety_violations",
    "ai_model_versions",
    "ai_prompt_versions",
    "ai_inference_metrics",
    "ai_job_status_events",
    "ai_human_feedback",
    "ai_output_lcr_proposals",
    "ai_output_lcr_decisions",
)


def upgrade():
    bind = op.get_bind()
    inspector = inspect(bind)
    # Add only missing tables. Existing tables and records are never dropped,
    # truncated, or recreated by this migration.
    missing = [
        Base.metadata.tables[name]
        for name in TABLES
        if not inspector.has_table(name)
    ]
    if missing:
        Base.metadata.create_all(bind=bind, tables=missing, checkfirst=True)


def downgrade():
    # AI review and LCR authorization history is safety-relevant audit data.
    # Do not silently delete it via an automatic downgrade.
    raise RuntimeError(
        "AI governance and LCR decision history is retained; "
        "use a reviewed backup and data-retention plan for any removal."
    )
