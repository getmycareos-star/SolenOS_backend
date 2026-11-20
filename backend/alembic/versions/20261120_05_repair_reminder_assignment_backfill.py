"""Repair reminder assignment defaults when the column pre-existed migration 01.

Revision ID: 20261120_05_reminder_assignment_repair
Revises: 20261120_04_caregiver_operation_permissions
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "20261120_05_reminder_assignment_repair"
down_revision = "20261120_04_caregiver_operation_permissions"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    if not inspector.has_table("care_reminders"):
        raise RuntimeError("care_reminders is missing; cannot repair reminder assignment")

    columns = {column["name"]: column for column in inspector.get_columns("care_reminders")}
    required = {"created_by_caregiver_id", "assigned_to_caregiver_id", "version"}
    missing = sorted(required - set(columns))
    if missing:
        raise RuntimeError(
            "Reminder assignment repair requires existing columns: " + ", ".join(missing)
        )

    # This backfill is deliberately unconditional. Migration 20261120_01 only
    # performed it when it created the assignment column, but the initial
    # metadata bootstrap can pre-create that column on a partially populated DB.
    bind.execute(sa.text(
        "UPDATE care_reminders "
        "SET assigned_to_caregiver_id = created_by_caregiver_id "
        "WHERE assigned_to_caregiver_id IS NULL"
    ))

    # The ORM contract requires a non-null optimistic-concurrency version.
    bind.execute(sa.text(
        "UPDATE care_reminders SET version = 1 WHERE version IS NULL"
    ))
    if columns["version"].get("nullable", True):
        op.alter_column(
            "care_reminders",
            "version",
            existing_type=sa.Integer(),
            nullable=False,
            server_default="1",
        )

    # Ensure the assignment reference is protected even when the column was
    # created by an earlier metadata bootstrap rather than migration 01.
    foreign_keys = inspect(bind).get_foreign_keys("care_reminders")
    has_assignment_fk = any(
        item.get("constrained_columns") == ["assigned_to_caregiver_id"]
        and item.get("referred_table") == "caregivers"
        and item.get("referred_columns") == ["id"]
        for item in foreign_keys
    )
    if not has_assignment_fk:
        orphan = bind.execute(sa.text(
            "SELECT reminder.assigned_to_caregiver_id "
            "FROM care_reminders AS reminder "
            "LEFT JOIN caregivers AS caregiver "
            "ON caregiver.id = reminder.assigned_to_caregiver_id "
            "WHERE reminder.assigned_to_caregiver_id IS NOT NULL "
            "AND caregiver.id IS NULL LIMIT 1"
        )).first()
        if orphan:
            raise RuntimeError(
                "Cannot add reminder assignee foreign key: orphan caregiver references exist"
            )
        op.create_foreign_key(
            "fk_care_reminders_assigned_to_caregiver_id_caregivers",
            "care_reminders",
            "caregivers",
            ["assigned_to_caregiver_id"],
            ["id"],
            ondelete="SET NULL",
        )

    indexes = {item["name"] for item in inspect(bind).get_indexes("care_reminders")}
    if "ix_care_reminders_assigned_to_caregiver_id" not in indexes:
        op.create_index(
            "ix_care_reminders_assigned_to_caregiver_id",
            "care_reminders",
            ["assigned_to_caregiver_id"],
            unique=False,
        )


def downgrade() -> None:
    # This migration only repairs existing rows and constraints. Reversing it
    # would reintroduce null ownership/version data and weaken referential
    # integrity, so intentionally leave the safe repair in place.
    pass
