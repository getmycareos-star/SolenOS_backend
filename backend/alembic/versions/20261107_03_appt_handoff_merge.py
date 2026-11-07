"""Merge appointment continuity and Companion handoff migration branches.

Revision ID: 20261107_03_appt_handoff_merge
Revises: 20261106_02_action_results, 20261107_02_handoff_drafts

This is a graph-only merge. Both branches retain their reviewed schema
changes; no caregiver records or application tables are modified here.
"""
revision = "20261107_03_appt_handoff_merge"
down_revision = (
    "20261106_02_action_results",
    "20261107_02_handoff_drafts",
)
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    raise RuntimeError("Refusing to split the canonical appointment and handoff migration graph.")
