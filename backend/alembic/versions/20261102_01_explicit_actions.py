"""Add caregiver-approved registered-action lifecycle and audit history.

Revision ID: 20261102_01_explicit_actions
Revises: 20261030_05_history_merge
"""
from alembic import op
import sqlalchemy as sa

revision = "20261102_01_explicit_actions"
down_revision = "20261030_05_history_merge"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("actions", sa.Column("registration_key", sa.String(length=128), nullable=True))
    op.add_column("actions", sa.Column("registration_request_sha256", sa.String(length=64), nullable=True))
    op.add_column("actions", sa.Column("source_evidence_ids", sa.Text(), nullable=True))
    op.add_column("actions", sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("actions", sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("actions", sa.Column("deferred_until", sa.DateTime(timezone=True), nullable=True))
    op.add_column("actions", sa.Column("version", sa.Integer(), nullable=False, server_default="1"))
    op.create_unique_constraint(
        "uq_actions_person_registration_key", "actions", ["person_id", "registration_key"]
    )

    op.add_column(
        "care_reminders",
        sa.Column("action_id", sa.String(), sa.ForeignKey("actions.id"), nullable=True),
    )
    op.create_index("ix_care_reminders_action_id", "care_reminders", ["action_id"], unique=True)

    op.create_table(
        "action_lifecycle_audit",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("action_id", sa.String(), sa.ForeignKey("actions.id"), nullable=False),
        sa.Column("person_id", sa.String(), sa.ForeignKey("persons.id"), nullable=False),
        sa.Column("actor_caregiver_id", sa.String(), sa.ForeignKey("caregivers.id"), nullable=False),
        sa.Column("previous_status", sa.String(), nullable=True),
        sa.Column("new_status", sa.String(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("evidence_ids", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_action_lifecycle_audit_action_id", "action_lifecycle_audit", ["action_id"])
    op.create_index("ix_action_lifecycle_audit_person_id", "action_lifecycle_audit", ["person_id"])
    op.create_index("ix_action_lifecycle_audit_actor_caregiver_id", "action_lifecycle_audit", ["actor_caregiver_id"])


def downgrade() -> None:
    raise RuntimeError("Registered-action approval and audit history are durable care records; downgrade requires a reviewed data-preservation migration.")
