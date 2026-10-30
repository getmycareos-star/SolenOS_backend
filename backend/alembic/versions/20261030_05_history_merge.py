"""Merge monotonic clarification-answer and situation-relationship history revisions.

Revision ID: 20261030_05_history_merge
Revises: 20261030_04_q_answer_version, 20261030_04_situation_relationship_versions
"""
revision = "20261030_05_history_merge"
down_revision = (
    "20261030_04_q_answer_version",
    "20261030_04_situation_relationship_versions",
)
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Both parent revisions independently add monotonic, append-only history
    # invariants. This revision reconciles the graph without changing data.
    pass


def downgrade() -> None:
    raise RuntimeError("Refusing to split the canonical care-history migration graph.")
