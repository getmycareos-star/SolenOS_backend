"""Persist interaction pause state independently from care reality.

Revision ID: 20261022_02
Revises: 20261022_01
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect, text

revision = "20261022_02"
down_revision = "20261022_01"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    if inspect(bind).has_table("interaction_sessions"):
        return
    op.create_table(
        "interaction_sessions",
        sa.Column("id", sa.String(), primary_key=True, nullable=False),
        sa.Column("interaction_session_id", sa.String(length=128), nullable=False),
        sa.Column("person_id", sa.String(), sa.ForeignKey("persons.id"), nullable=False),
        sa.Column("caregiver_id", sa.String(), sa.ForeignKey("caregivers.id"), nullable=False),
        sa.Column("paused_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint(
            "interaction_session_id",
            "person_id",
            "caregiver_id",
            name="uq_interaction_session_scope",
        ),
    )
    op.create_index(
        "ix_interaction_sessions_interaction_session_id",
        "interaction_sessions",
        ["interaction_session_id"],
    )
    op.create_index("ix_interaction_sessions_person_id", "interaction_sessions", ["person_id"])
    op.create_index("ix_interaction_sessions_caregiver_id", "interaction_sessions", ["caregiver_id"])


def downgrade():
    bind = op.get_bind()
    inspector = inspect(bind)
    if not inspector.has_table("interaction_sessions"):
        return
    count = bind.execute(text("SELECT COUNT(*) FROM interaction_sessions")).scalar() or 0
    if count:
        raise RuntimeError(
            "Cannot downgrade while interaction-session state exists; export or migrate "
            "the state through an approved recovery plan first."
        )
    op.drop_table("interaction_sessions")
