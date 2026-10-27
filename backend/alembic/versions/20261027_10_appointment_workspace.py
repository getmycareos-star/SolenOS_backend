"""Persist appointment preparation, visit notes, outcomes and follow-up with revisions.

The record is a workflow workspace linked to the LCR/person, not a second medical history.
Every update appends a revision and uses optimistic version checking.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "20261027_10_appt_workspace"
down_revision = "20261027_09_lcr_query_indexes"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    inspector = inspect(bind)
    if not inspector.has_table("appointment_workspace_records"):
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
    inspector = inspect(bind)
    record_indexes = {item["name"] for item in inspector.get_indexes("appointment_workspace_records")}
    if "ix_appointment_workspace_person" not in record_indexes:
        op.create_index("ix_appointment_workspace_person", "appointment_workspace_records", ["person_id", "updated_at"])
    if "ix_appointment_workspace_appointment" not in record_indexes:
        op.create_index("ix_appointment_workspace_appointment", "appointment_workspace_records", ["appointment_id"])

    if not inspector.has_table("appointment_workspace_revisions"):
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
        )
    inspector = inspect(bind)
    revision_indexes = {item["name"] for item in inspector.get_indexes("appointment_workspace_revisions")}
    if "uq_appointment_workspace_revision" not in revision_indexes:
        op.create_index(
            "uq_appointment_workspace_revision", "appointment_workspace_revisions",
            ["record_id", "revision_number"], unique=True,
        )
    if "ix_appointment_workspace_revision_person_time" not in revision_indexes:
        op.create_index("ix_appointment_workspace_revision_person_time", "appointment_workspace_revisions", ["person_id", "changed_at"])


def downgrade():
    # Appointment history is continuity evidence. Do not delete it automatically on rollback.
    pass
