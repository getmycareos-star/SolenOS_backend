"""Reconcile care-continuity tables after the legacy 20261022_03 revision collision.

This idempotent forward migration creates missing schema only. It never inserts
synthetic caregivers, persons, or care records.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect, text

revision = "20261024_05"
down_revision = "20261024_04"
branch_labels = None
depends_on = None


def _ensure_index(bind, table, name, columns):
    inspector = inspect(bind)
    if inspector.has_table(table):
        existing = {item["name"] for item in inspector.get_indexes(table)}
        if name not in existing:
            op.create_index(name, table, columns)


def upgrade():
    bind = op.get_bind()
    inspector = inspect(bind)
    if not inspector.has_table("persons") or not inspector.has_table("care_events"):
        raise RuntimeError("Cannot reconcile care-continuity schema: persons/care_events are absent.")

    if not inspector.has_table("care_state_revisions"):
        op.create_table(
            "care_state_revisions",
            sa.Column("id", sa.String(), primary_key=True, nullable=False),
            sa.Column("person_id", sa.String(), sa.ForeignKey("persons.id"), nullable=False),
            sa.Column("target_type", sa.String(), nullable=False),
            sa.Column("target_id", sa.String(), nullable=False),
            sa.Column("revision_number", sa.Integer(), nullable=False),
            sa.Column("change_kind", sa.String(), nullable=False),
            sa.Column("before_state", sa.Text()),
            sa.Column("after_state", sa.Text()),
            sa.Column("evidence_ids", sa.Text(), nullable=False),
            sa.Column("changed_by_caregiver_id", sa.String()),
            sa.Column("changed_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.UniqueConstraint("target_type", "target_id", "revision_number", name="uq_care_state_revision_target_version"),
        )
    _ensure_index(bind, "care_state_revisions", "ix_care_state_revisions_person_target", ["person_id", "target_type", "target_id"])
    _ensure_index(bind, "care_state_revisions", "ix_care_state_revisions_person_changed", ["person_id", "changed_at"])
    _ensure_index(bind, "care_state_revisions", "ix_care_state_revisions_person_id", ["person_id"])

    inspector = inspect(bind)
    if inspector.has_table("contradictions"):
        columns = {item["name"] for item in inspector.get_columns("contradictions")}
        if "source_assertion_ids" not in columns:
            op.add_column("contradictions", sa.Column("source_assertion_ids", sa.Text(), nullable=True))

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
            sa.Column("source_event_id", sa.String(), sa.ForeignKey("care_events.id")),
            sa.Column("source_label", sa.String(), nullable=False),
            sa.Column("source_text", sa.Text()),
            sa.Column("evidence_ids", sa.Text(), nullable=False),
            sa.Column("assertion_status", sa.String(), nullable=False, server_default="reported"),
            sa.Column("observed_at", sa.DateTime(timezone=True)),
            sa.Column("review_after_days", sa.Integer()),
            sa.Column("created_by_caregiver_id", sa.String()),
            sa.Column("updated_by_caregiver_id", sa.String()),
            sa.Column("recorded_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        )
    else:
        columns = {item["name"] for item in inspect(bind).get_columns("care_assertions")}
        if "updated_by_caregiver_id" not in columns:
            op.add_column("care_assertions", sa.Column("updated_by_caregiver_id", sa.String(), nullable=True))
    _ensure_index(bind, "care_assertions", "ix_care_assertions_subject_field", ["person_id", "subject_type", "subject_id", "field"])
    _ensure_index(bind, "care_assertions", "ix_care_assertions_person_id", ["person_id"])

    if not inspector.has_table("care_action_outcomes"):
        op.create_table(
            "care_action_outcomes",
            sa.Column("id", sa.String(), primary_key=True, nullable=False),
            sa.Column("person_id", sa.String(), sa.ForeignKey("persons.id"), nullable=False),
            sa.Column("action_id", sa.String(), nullable=False),
            sa.Column("action_type", sa.String(), nullable=False),
            sa.Column("outcome_status", sa.String(), nullable=False),
            sa.Column("notes", sa.Text()),
            sa.Column("correction_text", sa.Text()),
            sa.Column("evidence_ids", sa.Text(), nullable=False),
            sa.Column("created_by_caregiver_id", sa.String()),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        )
    _ensure_index(bind, "care_action_outcomes", "ix_care_action_outcomes_person_action", ["person_id", "action_type", "action_id"])
    _ensure_index(bind, "care_action_outcomes", "ix_care_action_outcomes_person_id", ["person_id"])


def downgrade():
    bind = op.get_bind()
    inspector = inspect(bind)
    for table in ("care_action_outcomes", "care_assertions", "care_state_revisions"):
        if inspector.has_table(table):
            count = bind.execute(text(f'SELECT COUNT(*) FROM "{table}"')).scalar() or 0
            if count:
                raise RuntimeError(f"Refusing to drop {table}: it contains {count} records.")
    for table in ("care_action_outcomes", "care_assertions", "care_state_revisions"):
        if inspect(bind).has_table(table):
            op.drop_table(table)
