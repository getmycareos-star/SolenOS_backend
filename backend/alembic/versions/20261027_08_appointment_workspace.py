"""Persist appointment preparation, visit notes, outcomes and follow-up with revisions.

The record is a workflow workspace linked to the LCR/person, not a second medical history.
Every update appends a revision and uses optimistic version checking.
"""
from alembic import op
import sqlalchemy as sa

revision = "20261027_08_appointment_workspace"
down_revision = "20261027_09_lcr_query_indexes"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "appointment_workspace_records",
        sa.Column("id", sa.String(36), primary_key=True, nullable=False),
        sa.Column("person_id", sa.String(36), sa.ForeignKey("persons.id", ondelete="CASCADE"), nullable=False),
        sa.Column("appointment_id", sa.String(36), sa.ForeignKey("appointments.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_by_caregiver_id", sa.String(36), sa.ForeignKey("caregivers.id", ondelete="SET NULL"), nullable=True),
        sa.Column("updated_by_caregiver_id", sa.String(36), sa.ForeignKey("caregivers.id", ondelete="SET NULL"), nullable=True),
        sa.Column("status", sa.String(40), nullable=False, server_default="preparation"),
        sa.Column("version_number", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("last_revision_reason", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_appointment_workspace_person", "appointment_workspace_records", ["person_id", "updated_at"])
    op.create_index("ix_appointment_workspace_appointment", "appointment_workspace_records", ["appointment_id"])
    op.create_table(
        "appointment_workspace_revisions",
        sa.Column("id", sa.String(36), primary_key=True, nullable=False),
        sa.Column("record_id", sa.String(36), sa.ForeignKey("appointment_workspace_records.id", ondelete="CASCADE"), nullable=False),
        sa.Column("person_id", sa.String(36), sa.ForeignKey("persons.id", ondelete="CASCADE"), nullable=False),
        sa.Column("revision_number", sa.Integer(), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("change_reason", sa.Text(), nullable=True),
        sa.Column("changed_by_caregiver_id", sa.String(36), sa.ForeignKey("caregivers.id", ondelete="SET NULL"), nullable=True),
        sa.Column("changed_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("record_id", "revision_number", name="uq_appointment_workspace_revision"),
    )
    op.create_index("ix_appointment_workspace_revision_person_time", "appointment_workspace_revisions", ["person_id", "changed_at"])


def downgrade():
    # Appointment history is continuity evidence. Do not delete it automatically on rollback.
    pass
