"""Persist queued Qwen background work.

Revision ID: 20261026_01_qwen_background_jobs
Revises: 20261025_04_caregiver_consents
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "20261026_01_qwen_background_jobs"
down_revision = "20261025_04_caregiver_consents"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    if not inspect(bind).has_table("qwen_background_jobs"):
        op.create_table(
            "qwen_background_jobs",
            sa.Column("id", sa.String(), primary_key=True, nullable=False),
            sa.Column("person_id", sa.String(), sa.ForeignKey("persons.id", ondelete="CASCADE"), nullable=False),
            sa.Column("caregiver_id", sa.String(), sa.ForeignKey("caregivers.id", ondelete="CASCADE"), nullable=False),
            sa.Column("status", sa.String(length=24), server_default="queued", nullable=False),
            sa.Column("task_text", sa.Text(), nullable=False),
            sa.Column("context_text", sa.Text(), server_default="", nullable=False),
            sa.Column("evidence_ids_json", sa.Text(), server_default="[]", nullable=False),
            sa.Column("appointment_id", sa.String(length=128), nullable=True),
            sa.Column("workflow", sa.String(length=48), nullable=True),
            sa.Column("result_text", sa.Text(), nullable=True),
            sa.Column("source_event_ids_json", sa.Text(), server_default="[]", nullable=False),
            sa.Column("source_evidence_ids_json", sa.Text(), server_default="[]", nullable=False),
            sa.Column("attempt_count", sa.Integer(), server_default="0", nullable=False),
            sa.Column("error_code", sa.String(length=48), nullable=True),
            sa.Column("available_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column("locked_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        )
        op.create_index("ix_qwen_background_jobs_person_id", "qwen_background_jobs", ["person_id"])
        op.create_index("ix_qwen_background_jobs_caregiver_id", "qwen_background_jobs", ["caregiver_id"])
        op.create_index("ix_qwen_background_jobs_status", "qwen_background_jobs", ["status"])
        op.create_index("ix_qwen_background_jobs_available_at", "qwen_background_jobs", ["available_at"])
        op.create_index("ix_qwen_background_jobs_claim", "qwen_background_jobs", ["status", "available_at", "created_at"])


def downgrade():
    bind = op.get_bind()
    if inspect(bind).has_table("qwen_background_jobs"):
        op.drop_table("qwen_background_jobs")
