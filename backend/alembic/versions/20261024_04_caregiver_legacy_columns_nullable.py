"""Reconcile the legacy caregiver schema and care-continuity tables.

Production reached revision 20261022_03 through a legacy caregiver-reconciliation
migration. The current feature branch reused that revision ID for care-continuity
DDL, so Alembic cannot infer that the care-continuity DDL was applied. This
forward-only migration repairs the missing schema idempotently, then relaxes
only legacy caregiver fields absent from the current ORM and lacking defaults.

Revision ID: 20261024_04
Revises: 20261023_05
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect, text

revision = "20261024_04"
down_revision = "20261023_05"
branch_labels = None
depends_on = None


def _ensure_index(bind, table_name, index_name, columns):
    inspector = inspect(bind)
    if not inspector.has_table(table_name):
        return
    existing = {item["name"] for item in inspector.get_indexes(table_name)}
    if index_name not in existing:
        op.create_index(index_name, table_name, columns)


def _ensure_care_continuity_schema(bind):
    inspector = inspect(bind)

    if not inspector.has_table("care_state_revisions"):
        op.create_table(
            "care_state_revisions",
            sa.Column("id", sa.String(), primary_key=True, nullable=False),
            sa.Column("person_id", sa.String(), sa.ForeignKey("persons.id"), nullable=False),
            sa.Column("target_type", sa.String(), nullable=False),
            sa.Column("target_id", sa.String(), nullable=False),
            sa.Column("revision_number", sa.Integer(), nullable=False),
            sa.Column("change_kind", sa.String(), nullable=False),
            sa.Column("before_state", sa.Text(), nullable=True),
            sa.Column("after_state", sa.Text(), nullable=True),
            sa.Column("evidence_ids", sa.Text(), nullable=False),
            sa.Column("changed_by_caregiver_id", sa.String(), nullable=True),
            sa.Column("changed_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.UniqueConstraint("target_type", "target_id", "revision_number", name="uq_care_state_revision_target_version"),
        )
    _ensure_index(bind, "care_state_revisions", "ix_care_state_revisions_person_target", ["person_id", "target_type", "target_id"])
    _ensure_index(bind, "care_state_revisions", "ix_care_state_revisions_person_changed", ["person_id", "changed_at"])
    _ensure_index(bind, "care_state_revisions", "ix_care_state_revisions_person_id", ["person_id"])

    inspector = inspect(bind)
    if inspector.has_table("contradictions"):
        columns = {column["name"] for column in inspector.get_columns("contradictions")}
        if "source_assertion_ids" not in columns:
            op.add_column("contradictions", sa.Column("source_assertion_ids", sa.Text(), nullable=True))

    inspector = inspect(bind)
    if not inspector.has_table("care_assertions"):
        op.create_table(
            "care_assertions",
            sa.Column("id", sa.String(), primary_key=True, nullable=False),
            sa.Column("person_id", sa.String(), sa.ForeignKey("persons.id"), nullable=False),
            sa.Column("subject_type", sa.String(), nullable=False),
            sa.Column("subject_id", sa.String(), nullable=False),
            sa.Column("field", sa.String(), nullable=False),
            sa.Column("assertion_kind", sa.String(), nullable=False, server_default="current_state"),
            sa.Column("asserted_value", sa.Text(), nullable=False),
            sa.Column("source_event_id", sa.String(), sa.ForeignKey("care_events.id"), nullable=True),
            sa.Column("source_label", sa.String(), nullable=False),
            sa.Column("source_text", sa.Text(), nullable=True),
            sa.Column("evidence_ids", sa.Text(), nullable=False),
            sa.Column("assertion_status", sa.String(), nullable=False, server_default="reported"),
            sa.Column("observed_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("review_after_days", sa.Integer(), nullable=True),
            sa.Column("created_by_caregiver_id", sa.String(), nullable=True),
            sa.Column("updated_by_caregiver_id", sa.String(), nullable=True),
            sa.Column("recorded_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        )
    else:
        columns = {column["name"] for column in inspect(bind).get_columns("care_assertions")}
        if "updated_by_caregiver_id" not in columns:
            op.add_column("care_assertions", sa.Column("updated_by_caregiver_id", sa.String(), nullable=True))
    _ensure_index(bind, "care_assertions", "ix_care_assertions_subject_field", ["person_id", "subject_type", "subject_id", "field"])
    _ensure_index(bind, "care_assertions", "ix_care_assertions_person_id", ["person_id"])

    inspector = inspect(bind)
    if not inspector.has_table("care_action_outcomes"):
        op.create_table(
            "care_action_outcomes",
            sa.Column("id", sa.String(), primary_key=True, nullable=False),
            sa.Column("person_id", sa.String(), sa.ForeignKey("persons.id"), nullable=False),
            sa.Column("action_id", sa.String(), nullable=False),
            sa.Column("action_type", sa.String(), nullable=False),
            sa.Column("outcome_status", sa.String(), nullable=False),
            sa.Column("notes", sa.Text(), nullable=True),
            sa.Column("correction_text", sa.Text(), nullable=True),
            sa.Column("evidence_ids", sa.Text(), nullable=False),
            sa.Column("created_by_caregiver_id", sa.String(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        )
    _ensure_index(bind, "care_action_outcomes", "ix_care_action_outcomes_person_action", ["person_id", "action_type", "action_id"])
    _ensure_index(bind, "care_action_outcomes", "ix_care_action_outcomes_person_id", ["person_id"])


def upgrade():
    bind = op.get_bind()
    inspector = inspect(bind)
    if not inspector.has_table("caregivers"):
        raise RuntimeError("Cannot reconcile caregiver constraints: public.caregivers does not exist.")

    _ensure_care_continuity_schema(bind)

    # These legacy account-era columns are not represented or written by the
    # care-record ORM. Preserve their data and constraints except for NOT NULL.
    inspector = inspect(bind)
    columns = {column["name"]: column for column in inspector.get_columns("caregivers")}
    for column_name in ("password_hash", "display_name"):
        column = columns.get(column_name)
        if column is None or column.get("nullable", True) or column.get("primary_key"):
            continue
        op.alter_column(
            "caregivers",
            column_name,
            existing_type=column["type"],
            nullable=True,
        )
        # Refresh the metadata before checking the next column.
        columns = {item["name"]: item for item in inspect(bind).get_columns("caregivers")}


def downgrade():
    # This migration reconciles schema that may have been created by the ORM
    # before Alembic owned it. Ownership of each table/column is ambiguous, so
    # automatic reversal could delete care data or remove pre-existing schema.
    # Refuse rather than silently claiming a downgrade that did not occur.
    raise RuntimeError(
        "20261024_04 is a forward-only production schema reconciliation. "
        "Do not downgrade automatically; use a separately reviewed migration "
        "after verifying table ownership and all affected data."
    )
