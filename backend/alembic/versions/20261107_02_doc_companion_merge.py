"""Merge document review and Companion LCR proposal migrations.

Revision ID: 20261107_02_doc_companion_merge
Revises: 20261106_01_doc_review_events, 20261107_01_companion_lcr_review
"""

revision = "20261107_02_doc_companion_merge"
down_revision = (
    "20261106_01_doc_review_events",
    "20261107_01_companion_lcr_review",
)
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    # This revision only joins two independent migration branches.
    pass
