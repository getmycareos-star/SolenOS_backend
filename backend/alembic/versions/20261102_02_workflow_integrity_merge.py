"""Merge explicit-action and clarification-review migrations.

Revision ID: 20261102_02_workflow_integrity_merge
Revises: 20261102_01_explicit_actions, 20261102_01_q_review_events

The merge widens Alembic's version column before Alembic stamps this revision.
PostgreSQL creates that bookkeeping column as VARCHAR(32), while this
canonical revision identifier is longer.
"""
from alembic import op

revision = "20261102_02_workflow_integrity_merge"
down_revision = (
    "20261102_01_explicit_actions",
    "20261102_01_q_review_events",
)
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Alembic creates version_num as VARCHAR(32) by default. This revision
    # identifier exceeds that limit, so widen the bookkeeping column before
    # Alembic attempts to update it after upgrade() returns. Keep the revision
    # identifier stable for databases that have already recorded it.
    if op.get_bind().dialect.name == "postgresql":
        op.execute("ALTER TABLE alembic_version ALTER COLUMN version_num TYPE VARCHAR(128)")


def downgrade() -> None:
    raise RuntimeError("Refusing to split the canonical workflow-integrity migration graph.")
