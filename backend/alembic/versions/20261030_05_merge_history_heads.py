"""Merge the independent answer-history and situation-relationship version branches.

Revision ID: 20261030_05_merge_history_heads
Revises: 20261030_04_q_answer_version, 20261030_04_sit_rel_ver
"""

revision = "20261030_05_merge_history_heads"
down_revision = (
    "20261030_04_q_answer_version",
    "20261030_04_sit_rel_ver",
)
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Both parent migrations are independent additive schema changes.
    # This revision intentionally joins their histories without altering data.
    pass


def downgrade() -> None:
    # Alembic downgrades each parent branch from this merge point.
    pass
