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
    pass


def downgrade() -> None:
    raise RuntimeError("Refusing to split the canonical workflow-integrity migration graph.")
