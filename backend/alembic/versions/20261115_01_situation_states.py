"""Add caregiver-confirmed improving and worsening situation states.

Revision ID: 20261115_01_situation_states
Revises: 20261112_07_caregiver_access

PostgreSQL stores SQLAlchemy enum member names (uppercase). Existing databases
may already have the enum without these newer labels, so add only the missing
values. PostgreSQL enum values are intentionally not removed on downgrade:
removing them can invalidate persisted situation history.
"""
from alembic import op

revision = "20261115_01_situation_states"
down_revision = "20261112_07_caregiver_access"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return

    # The type is created by the original situations migration. Keep this
    # repair idempotent for databases whose enum was already updated manually.
    op.execute("ALTER TYPE situationstate ADD VALUE IF NOT EXISTS 'WORSENING'")
    op.execute("ALTER TYPE situationstate ADD VALUE IF NOT EXISTS 'IMPROVING'")


def downgrade():
    # PostgreSQL does not support safely removing enum labels in-place.
    # Retain the labels so historical caregiver-confirmed states remain valid.
    pass
