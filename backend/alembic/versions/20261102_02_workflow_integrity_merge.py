from alembic import op
import sqlalchemy as sa

"""Merge explicit-action and clarification-review migrations.

Revision ID: 20261102_02_wf_integrity_merge
Revises: 20261102_01_explicit_actions, 20261102_01_q_review_events

This is a graph-only reconciliation. Both child migrations remain in the
canonical history; no care data or schema objects are modified here.
"""
revision = "20261102_02_wf_integrity_merge"
down_revision = (
    "20261102_01_explicit_actions",
    "20261102_01_q_review_events",
)
branch_labels = None
depends_on = None


def upgrade() -> None:
    # PostgreSQL's default Alembic version column is VARCHAR(32), but the
    # canonical merge revision is longer. Widen migration bookkeeping before
    # Alembic writes this revision; no application or care data is changed.
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.alter_column(
            "alembic_version",
            "version_num",
            existing_type=sa.String(length=32),
            type_=sa.String(length=128),
            existing_nullable=False,
        )


def downgrade() -> None:
    raise RuntimeError("Refusing to split the canonical workflow-integrity migration graph.")
