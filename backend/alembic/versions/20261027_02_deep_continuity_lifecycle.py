"""Add explicit follow-up, review, provenance, timeline and change-assessment records.

Revision ID: 20261027_02_deep_continuity_lifecycle
Revises: 20261027_01_care_domain_reconcile

These are added only where the existing models do not express the lifecycle
relationship directly. Existing evidence, event, change and revision rows remain
the source records; timeline entries are projections with explicit source links.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "20261027_02_deep_continuity_lifecycle"
down_revision = "20261027_01_care_domain_reconcile"
branch_labels = None
depends_on = None

NOW = sa.func.now()


def _create(bind, name, columns, indexes=()):
    if inspect(bind).has_table(name):
        return
    op.create_table(name, sa.Column("id", sa.String(36), primary_key=True, nullable=False), *columns)
    for index_name, index_columns, unique in indexes:
        op.create_index(index_name, name, index_columns, unique=unique)


def upgrade():
    bind = op.get_bind()
    s, txt, dt, js = sa.String, sa.Text, sa.DateTime(timezone=True), sa.JSON

    _create(bind, "care_follow_up_links", [
        sa.Column("person_id", s(36), sa.ForeignKey("persons.id", ondelete="CASCADE"), nullable=False),
        sa.Column("source_type", s(80), nullable=False),
        sa.Column("source_id", s(36), nullable=False),
        sa.Column("target_type", s(80), nullable=False),
        sa.Column("target_id", s(36), nullable=False),
        sa.Column("follow_up_kind", s(80), nullable=False, server_default="review"),
        sa.Column("status", s(32), nullable=False, server_default="open"),
        sa.Column("due_at", dt, nullable=True),
        sa.Column("completed_at", dt, nullable=True),
        sa.Column("notes", txt, nullable=True),
        sa.Column("evidence_ids", js, nullable=False, server_default=sa.text("'[]'")),
        sa.Column("created_by_caregiver_id", s(36), sa.ForeignKey("caregivers.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_at", dt, nullable=False, server_default=NOW),
        sa.Column("updated_at", dt, nullable=True),
    ], [("ix_care_follow_up_links_person_status_due", ["person_id", "status", "due_at"], False),
        ("ix_care_follow_up_links_source", ["person_id", "source_type", "source_id"], False)])

    _create(bind, "care_review_decisions", [
        sa.Column("person_id", s(36), sa.ForeignKey("persons.id", ondelete="CASCADE"), nullable=False),
        sa.Column("target_type", s(80), nullable=False),
        sa.Column("target_id", s(36), nullable=False),
        sa.Column("review_decision", s(40), nullable=False),
        sa.Column("reason", txt, nullable=True),
        sa.Column("evidence_ids", js, nullable=False, server_default=sa.text("'[]'")),
        sa.Column("reviewed_by_caregiver_id", s(36), sa.ForeignKey("caregivers.id", ondelete="SET NULL"), nullable=True),
        sa.Column("reviewed_at", dt, nullable=False, server_default=NOW),
        sa.Column("created_at", dt, nullable=False, server_default=NOW),
    ], [("ix_care_review_decisions_person_target", ["person_id", "target_type", "target_id"], False)])

    _create(bind, "care_provenance_records", [
        sa.Column("person_id", s(36), sa.ForeignKey("persons.id", ondelete="CASCADE"), nullable=False),
        sa.Column("target_type", s(80), nullable=False),
        sa.Column("target_id", s(36), nullable=False),
        sa.Column("source_type", s(80), nullable=False),
        sa.Column("source_id", s(36), nullable=True),
        sa.Column("evidence_id", s(36), sa.ForeignKey("evidence.id", ondelete="SET NULL"), nullable=True),
        sa.Column("source_label", s(240), nullable=True),
        sa.Column("source_span", txt, nullable=True),
        sa.Column("recorded_by_caregiver_id", s(36), sa.ForeignKey("caregivers.id", ondelete="SET NULL"), nullable=True),
        sa.Column("recorded_at", dt, nullable=False, server_default=NOW),
        sa.Column("metadata", js, nullable=False, server_default=sa.text("'{}'")),
    ], [("ix_care_provenance_target", ["person_id", "target_type", "target_id"], False),
        ("ix_care_provenance_source", ["person_id", "source_type", "source_id"], False)])

    _create(bind, "care_timeline_entries", [
        sa.Column("person_id", s(36), sa.ForeignKey("persons.id", ondelete="CASCADE"), nullable=False),
        sa.Column("source_type", s(80), nullable=False),
        sa.Column("source_id", s(36), nullable=False),
        sa.Column("entry_type", s(80), nullable=False),
        sa.Column("title", s(240), nullable=False),
        sa.Column("summary", txt, nullable=True),
        sa.Column("occurred_at", dt, nullable=True),
        sa.Column("recorded_at", dt, nullable=False, server_default=NOW),
        sa.Column("evidence_ids", js, nullable=False, server_default=sa.text("'[]'")),
        sa.Column("epistemic_status", s(32), nullable=False, server_default="reported"),
        sa.Column("is_projection", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", dt, nullable=False, server_default=NOW),
    ], [("ix_care_timeline_person_occurred", ["person_id", "occurred_at"], False),
        ("ix_care_timeline_source", ["person_id", "source_type", "source_id"], False)])

    _create(bind, "care_change_assessments", [
        sa.Column("person_id", s(36), sa.ForeignKey("persons.id", ondelete="CASCADE"), nullable=False),
        sa.Column("change_id", s(36), sa.ForeignKey("changes.id", ondelete="CASCADE"), nullable=False),
        sa.Column("assessment_status", s(32), nullable=False, server_default="pending_review"),
        sa.Column("significance", s(32), nullable=False, server_default="unknown"),
        sa.Column("evidence_status", s(32), nullable=False, server_default="unknown"),
        sa.Column("assessment_notes", txt, nullable=True),
        sa.Column("evidence_ids", js, nullable=False, server_default=sa.text("'[]'")),
        sa.Column("assessed_by_caregiver_id", s(36), sa.ForeignKey("caregivers.id", ondelete="SET NULL"), nullable=True),
        sa.Column("assessed_at", dt, nullable=False, server_default=NOW),
        sa.Column("created_at", dt, nullable=False, server_default=NOW),
    ], [("ix_care_change_assessments_person_change", ["person_id", "change_id"], False)])


def downgrade():
    # Keep production records intact; rollback is intentionally non-destructive.
    pass
