"""Add durable care-continuity assertions, state revisions, and outcomes.

Revision ID: 20261022_03
Revises: 20261022_02

The migration is additive. It does not rewrite existing care records.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect, text

revision = "20261022_03"
down_revision = "20261022_02"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
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
    inspector = inspect(bind)
    revision_indexes = {item["name"] for item in inspector.get_indexes("care_state_revisions")}
    if "ix_care_state_revisions_person_target" not in revision_indexes:
        op.create_index("ix_care_state_revisions_person_target", "care_state_revisions", ["person_id", "target_type", "target_id"])
    if "ix_care_state_revisions_person_changed" not in revision_indexes:
        op.create_index("ix_care_state_revisions_person_changed", "care_state_revisions", ["person_id", "changed_at"])
    if "ix_care_state_revisions_person_id" not in revision_indexes:
        op.create_index("ix_care_state_revisions_person_id", "care_state_revisions", ["person_id"])

    # Existing uncertainty records gain a link to the source assertions that conflict.
    if inspector.has_table("contradictions"):
        contradiction_columns = {column["name"] for column in inspector.get_columns("contradictions")}
        if "source_assertion_ids" not in contradiction_columns:
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
    inspector = inspect(bind)
    assertion_indexes = {item["name"] for item in inspector.get_indexes("care_assertions")}
    if "ix_care_assertions_subject_field" not in assertion_indexes:
        op.create_index("ix_care_assertions_subject_field", "care_assertions", ["person_id", "subject_type", "subject_id", "field"])
    if "ix_care_assertions_person_id" not in assertion_indexes:
        op.create_index("ix_care_assertions_person_id", "care_assertions", ["person_id"])

    inspector = inspect(bind)
    if inspector.has_table("care_assertions"):
        assertion_columns = {column["name"] for column in inspector.get_columns("care_assertions")}
        if "updated_by_caregiver_id" not in assertion_columns:
            op.add_column("care_assertions", sa.Column("updated_by_caregiver_id", sa.String(), nullable=True))

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
    inspector = inspect(bind)
    outcome_indexes = {item["name"] for item in inspector.get_indexes("care_action_outcomes")}
    if "ix_care_action_outcomes_person_action" not in outcome_indexes:
        op.create_index("ix_care_action_outcomes_person_action", "care_action_outcomes", ["person_id", "action_type", "action_id"])
    if "ix_care_action_outcomes_person_id" not in outcome_indexes:
        op.create_index("ix_care_action_outcomes_person_id", "care_action_outcomes", ["person_id"])


def downgrade():
    bind = op.get_bind()
    inspector = inspect(bind)
    for table in ("care_action_outcomes", "care_assertions", "care_state_revisions"):
        if inspector.has_table(table):
            count = bind.execute(text(f'SELECT COUNT(*) FROM "{table}"')).scalar() or 0
            if count:
                raise RuntimeError(
                    f"Refusing to drop {table}: it contains {count} care-continuity records. "
                    "Export or migrate them through an approved recovery plan first."
                )
    inspector = inspect(bind)
    if inspector.has_table("care_action_outcomes"):
        op.drop_table("care_action_outcomes")
    if inspector.has_table("care_assertions"):
        op.drop_table("care_assertions")
    if inspector.has_table("care_state_revisions"):
        op.drop_table("care_state_revisions")
