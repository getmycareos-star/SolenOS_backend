"""Stage consented improvement feedback for review.

Revision ID: 20261026_02_improvement_training_examples
Revises: 20261026_01_qwen_background_jobs
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "20261026_02_improvement_training_examples"
down_revision = "20261026_01_qwen_background_jobs"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    inspector = inspect(bind)
    if not inspector.has_table("improvement_training_examples"):
        op.create_table(
            "improvement_training_examples",
            sa.Column("id", sa.String(), primary_key=True, nullable=False),
            sa.Column("person_id", sa.String(), sa.ForeignKey("persons.id", ondelete="CASCADE"), nullable=False),
            sa.Column("caregiver_id", sa.String(), sa.ForeignKey("caregivers.id", ondelete="CASCADE"), nullable=False),
            sa.Column("category", sa.String(length=80), nullable=False),
            sa.Column("sample_text", sa.Text(), nullable=False),
            sa.Column("status", sa.String(length=24), nullable=False, server_default="pending_review"),
            sa.Column("consent_version", sa.String(length=64), nullable=False),
            sa.Column("consented_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        )
        op.create_index("ix_improvement_training_examples_person_id", "improvement_training_examples", ["person_id"])
        op.create_index("ix_improvement_training_examples_caregiver_id", "improvement_training_examples", ["caregiver_id"])
        op.create_index("ix_improvement_training_examples_status", "improvement_training_examples", ["status"])


def downgrade():
    bind = op.get_bind()
    if inspect(bind).has_table("improvement_training_examples"):
        op.drop_table("improvement_training_examples")
