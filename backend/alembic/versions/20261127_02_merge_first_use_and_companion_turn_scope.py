"""Merge first-use orientation and Companion idempotency migration heads.

Revision ID: 20261127_02_merge_first_use_and_companion_turn_scope
Revises: 20261127_01_caregiver_first_use_orientation, 20261127_01_scope_companion_turn_idempotency
"""
revision = "20261127_02_merge_first_use_and_companion_turn_scope"
down_revision = (
    "20261127_01_caregiver_first_use_orientation",
    "20261127_01_scope_companion_turn_idempotency",
)
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Both parent revisions are schema-only migrations and have already applied
    # their changes. This revision joins their Alembic histories.
    pass


def downgrade() -> None:
    # A merge revision has no independent schema changes to reverse.
    pass
