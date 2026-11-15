"""Idempotently verify caregiver-confirmed situation trajectory enum labels.

Revision ID: 20261115_01b_situation_states
Revises: 20261115_01_situation_states

The canonical situation-state migration already adds these PostgreSQL enum
labels. This follow-on revision is retained only to preserve the dependency
chain of the caregiver-transfer repair migration created alongside it.
"""
from alembic import op

revision = "20261115_01b_situation_states"
down_revision = "20261115_01_situation_states"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return
    op.execute("ALTER TYPE situationstate ADD VALUE IF NOT EXISTS 'WORSENING'")
    op.execute("ALTER TYPE situationstate ADD VALUE IF NOT EXISTS 'IMPROVING'")


def downgrade():
    # PostgreSQL enum labels may be referenced by persisted historical rows.
    pass
