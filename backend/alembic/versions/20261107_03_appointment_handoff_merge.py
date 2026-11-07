"""Merge appointment idempotency and handoff-draft migration branches.

Revision ID: 20261107_03_appt_handoff_merge
Revises: 20261105_02_appt_rev_idem, 20261107_02_handoff_drafts

Both branches retain their reviewed schema changes; this graph-only merge
performs no application data changes.
"""
revision = "20261107_03_appt_handoff_merge"
down_revision = (
    "20261105_02_appt_rev_idem",
    "20261107_02_handoff_drafts",
)
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    # This is a graph-only merge: Alembic may safely split the heads while
    # downgrading because this revision itself owns no schema or data changes.
    pass
