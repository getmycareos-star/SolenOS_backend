"""Add caregiver-confirmed situation trajectory states.

Revision ID: 20261115_01_situation_states
Revises: 20261112_10_caregiver_access_scope
"""
from alembic import op

revision = "20261115_01_situation_states"
down_revision = "20261112_10_caregiver_access_scope"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return
    # SQLAlchemy persists Python enum member names in the native PostgreSQL
    # enum. These states are accepted by the API transition graph and must
    # exist before a caregiver can save a trajectory update.
    op.execute("ALTER TYPE situationstate ADD VALUE IF NOT EXISTS 'WORSENING'")
    op.execute("ALTER TYPE situationstate ADD VALUE IF NOT EXISTS 'IMPROVING'")


def downgrade():
    # Removing PostgreSQL enum labels is unsafe once persisted records may
    # reference them. Retain the values on downgrade rather than lose data.
    pass
