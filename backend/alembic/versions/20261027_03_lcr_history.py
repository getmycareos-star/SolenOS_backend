"""Add missing LCR history tables on the canonical care-continuity migration head.

This migration is additive and idempotent for table creation. Existing care tables
and rows are retained. It deliberately does not create aliases for existing
evidence/events/transitions/delta structures.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "20261027_03_lcr_history"
down_revision = "20261027_02_continuity_lifecycle"
branch_labels = None
depends_on = None


def _create(bind, name, columns, indexes=()):
    if inspect(bind).has_table(name):
        return
    op.create_table(
        name,
        sa.Column("id", sa.String(36), primary_key=True, nullable=False),
        *columns,
    )
    for index_name, index_columns, unique in indexes:
        op.create_index(index_name, name, index_columns, unique=unique)


def upgrade():
    bind = op.get_bind()
    s, txt, dt, js = sa.String, sa.Text, sa.DateTime(timezone=True), sa.JSON
    now = sa.func.now()

    # These are genuinely separate history/lineage concepts; the existing
    # care_events, evidence, care_delta_runs, care_facts and upload tables remain
    # canonical for their respective domains.
    _create(bind, "living_care_records", [
        sa.Column("person_id", s(36), sa.ForeignKey("persons.id", ondelete="CASCADE"), nullable=False),
        sa.Column("record_status", s(32), nullable=False, server_default="active"),
        sa.Column("schema_version", s(40), nullable=False, server_default="lcr.v1"),
        sa.Column("created_at", dt, nullable=False, server_default=now),
        sa.Column("updated_at", dt, nullable=False, server_default=now),
        sa.Column("created_by_caregiver_id", s(36), sa.ForeignKey("caregivers.id", ondelete="SET NULL")),
    ], [("ix_living_care_records_person", ["person_id"], True)])

    _create(bind, "care_record_versions", [
        sa.Column("care_record_id", s(36), sa.ForeignKey("living_care_records.id", ondelete="CASCADE"), nullable=False),
        sa.Column("version_number", sa.Integer(), nullable=False),
        sa.Column("change_set_id", s(36), nullable=True),
        sa.Column("record_digest", s(128), nullable=True),
        sa.Column("snapshot_payload", js, nullable=False),
        sa.Column("created_at", dt, nullable=False, server_default=now),
        sa.Column("created_by_caregiver_id", s(36), sa.ForeignKey("caregivers.id", ondelete="SET NULL")),
        sa.Column("reason", txt, nullable=True),
    ], [("uq_care_record_version", ["care_record_id", "version_number"], True)])

    _create(bind, "care_record_snapshots", [
        sa.Column("care_record_id", s(36), sa.ForeignKey("living_care_records.id", ondelete="CASCADE"), nullable=False),
        sa.Column("version_id", s(36), sa.ForeignKey("care_record_versions.id", ondelete="SET NULL")),
        sa.Column("as_of", dt, nullable=False),
        sa.Column("snapshot_payload", js, nullable=False),
        sa.Column("record_digest", s(128), nullable=True),
        sa.Column("created_at", dt, nullable=False, server_default=now),
        sa.Column("created_by_caregiver_id", s(36), sa.ForeignKey("caregivers.id", ondelete="SET NULL")),
    ], [("ix_care_record_snapshots_asof", ["care_record_id", "as_of"], False)])

    _create(bind, "record_provenance", [
        sa.Column("person_id", s(36), sa.ForeignKey("persons.id", ondelete="CASCADE"), nullable=False),
        sa.Column("care_record_id", s(36), sa.ForeignKey("living_care_records.id", ondelete="CASCADE"), nullable=True),
        sa.Column("source_type", s(80), nullable=False),
        sa.Column("source_id", s(36), nullable=True),
        sa.Column("author_caregiver_id", s(36), sa.ForeignKey("caregivers.id", ondelete="SET NULL")),
        sa.Column("author_label", s(240), nullable=True),
        sa.Column("method", s(80), nullable=False, server_default="manual"),
        sa.Column("recorded_at", dt, nullable=False, server_default=now),
        sa.Column("occurred_at", dt, nullable=True),
        sa.Column("effective_from", dt, nullable=True),
        sa.Column("effective_to", dt, nullable=True),
        sa.Column("reason", txt, nullable=True),
        sa.Column("metadata", js, nullable=False, server_default=sa.text("'{}'")),
        sa.Column("created_at", dt, nullable=False, server_default=now),
    ], [("ix_record_provenance_person_time", ["person_id", "recorded_at"], False)])

    _create(bind, "record_provenance_links", [
        sa.Column("person_id", s(36), sa.ForeignKey("persons.id", ondelete="CASCADE"), nullable=False),
        sa.Column("provenance_id", s(36), sa.ForeignKey("record_provenance.id", ondelete="CASCADE"), nullable=False),
        sa.Column("target_type", s(100), nullable=False),
        sa.Column("target_id", s(36), nullable=False),
        sa.Column("relationship", s(40), nullable=False, server_default="originates"),
        sa.Column("created_at", dt, nullable=False, server_default=now),
    ], [("ix_record_provenance_links_target", ["person_id", "target_type", "target_id"], False)])

    _create(bind, "care_record_change_sets", [
        sa.Column("person_id", s(36), sa.ForeignKey("persons.id", ondelete="CASCADE"), nullable=False),
        sa.Column("care_record_id", s(36), sa.ForeignKey("living_care_records.id", ondelete="CASCADE"), nullable=True),
        sa.Column("operation", s(60), nullable=False),
        sa.Column("status", s(32), nullable=False, server_default="committed"),
        sa.Column("reason", txt, nullable=True),
        sa.Column("author_caregiver_id", s(36), sa.ForeignKey("caregivers.id", ondelete="SET NULL")),
        sa.Column("provenance_id", s(36), sa.ForeignKey("record_provenance.id", ondelete="SET NULL")),
        sa.Column("created_at", dt, nullable=False, server_default=now),
    ], [("ix_care_record_change_sets_person_time", ["person_id", "created_at"], False)])

    _create(bind, "care_record_change_items", [
        sa.Column("person_id", s(36), sa.ForeignKey("persons.id", ondelete="CASCADE"), nullable=False),
        sa.Column("change_set_id", s(36), sa.ForeignKey("care_record_change_sets.id", ondelete="CASCADE"), nullable=False),
        sa.Column("target_type", s(100), nullable=False),
        sa.Column("target_id", s(36), nullable=False),
        sa.Column("operation", s(32), nullable=False),
        sa.Column("before_value", js, nullable=True),
        sa.Column("after_value", js, nullable=True),
        sa.Column("provenance_id", s(36), sa.ForeignKey("record_provenance.id", ondelete="SET NULL")),
        sa.Column("created_at", dt, nullable=False, server_default=now),
    ], [("ix_care_record_change_items_target", ["person_id", "target_type", "target_id"], False)])

    _create(bind, "care_corrections", [
        sa.Column("person_id", s(36), sa.ForeignKey("persons.id", ondelete="CASCADE"), nullable=False),
        sa.Column("care_record_id", s(36), sa.ForeignKey("living_care_records.id", ondelete="CASCADE"), nullable=True),
        sa.Column("target_type", s(100), nullable=False),
        sa.Column("target_id", s(36), nullable=False),
        sa.Column("original_value", js, nullable=True),
        sa.Column("corrected_value", js, nullable=False),
        sa.Column("correction_reason", txt, nullable=False),
        sa.Column("status", s(32), nullable=False, server_default="recorded"),
        sa.Column("requested_by_caregiver_id", s(36), sa.ForeignKey("caregivers.id", ondelete="SET NULL")),
        sa.Column("reviewed_by_caregiver_id", s(36), sa.ForeignKey("caregivers.id", ondelete="SET NULL")),
        sa.Column("provenance_id", s(36), sa.ForeignKey("record_provenance.id", ondelete="SET NULL")),
        sa.Column("created_at", dt, nullable=False, server_default=now),
        sa.Column("effective_at", dt, nullable=True),
    ], [("ix_care_corrections_person_target", ["person_id", "target_type", "target_id"], False)])

    _create(bind, "record_amendments", [
        sa.Column("person_id", s(36), sa.ForeignKey("persons.id", ondelete="CASCADE"), nullable=False),
        sa.Column("care_record_id", s(36), sa.ForeignKey("living_care_records.id", ondelete="CASCADE"), nullable=True),
        sa.Column("amendment_type", s(80), nullable=False),
        sa.Column("reason", txt, nullable=False),
        sa.Column("status", s(32), nullable=False, server_default="recorded"),
        sa.Column("author_caregiver_id", s(36), sa.ForeignKey("caregivers.id", ondelete="SET NULL")),
        sa.Column("provenance_id", s(36), sa.ForeignKey("record_provenance.id", ondelete="SET NULL")),
        sa.Column("created_at", dt, nullable=False, server_default=now),
    ], [("ix_record_amendments_person_time", ["person_id", "created_at"], False)])

    _create(bind, "record_amendment_items", [
        sa.Column("person_id", s(36), sa.ForeignKey("persons.id", ondelete="CASCADE"), nullable=False),
        sa.Column("amendment_id", s(36), sa.ForeignKey("record_amendments.id", ondelete="CASCADE"), nullable=False),
        sa.Column("target_type", s(100), nullable=False),
        sa.Column("target_id", s(36), nullable=False),
        sa.Column("field_name", s(160), nullable=True),
        sa.Column("before_value", js, nullable=True),
        sa.Column("after_value", js, nullable=True),
        sa.Column("created_at", dt, nullable=False, server_default=now),
    ], [("ix_record_amendment_items_target", ["person_id", "target_type", "target_id"], False)])

    _create(bind, "care_record_audit_log", [
        sa.Column("person_id", s(36), sa.ForeignKey("persons.id", ondelete="CASCADE"), nullable=False),
        sa.Column("care_record_id", s(36), sa.ForeignKey("living_care_records.id", ondelete="CASCADE"), nullable=True),
        sa.Column("actor_type", s(40), nullable=False),
        sa.Column("actor_id", s(36), nullable=True),
        sa.Column("action", s(120), nullable=False),
        sa.Column("target_type", s(100), nullable=True),
        sa.Column("target_id", s(36), nullable=True),
        sa.Column("outcome", s(32), nullable=False, server_default="success"),
        sa.Column("reason", txt, nullable=True),
        sa.Column("metadata", js, nullable=False, server_default=sa.text("'{}'")),
        sa.Column("occurred_at", dt, nullable=False, server_default=now),
        sa.Column("request_id", s(120), nullable=True),
    ], [("ix_care_record_audit_person_time", ["person_id", "occurred_at"], False),
        ("ix_care_record_audit_target", ["target_type", "target_id"], False)])

    _create(bind, "care_uncertainties", [
        sa.Column("person_id", s(36), sa.ForeignKey("persons.id", ondelete="CASCADE"), nullable=False),
        sa.Column("care_record_id", s(36), sa.ForeignKey("living_care_records.id", ondelete="CASCADE"), nullable=True),
        sa.Column("target_type", s(100), nullable=True),
        sa.Column("target_id", s(36), nullable=True),
        sa.Column("question", txt, nullable=False),
        sa.Column("uncertainty_type", s(60), nullable=False),
        sa.Column("status", s(32), nullable=False, server_default="open"),
        sa.Column("source_ids", js, nullable=False, server_default=sa.text("'[]'")),
        sa.Column("created_by_caregiver_id", s(36), sa.ForeignKey("caregivers.id", ondelete="SET NULL")),
        sa.Column("created_at", dt, nullable=False, server_default=now),
        sa.Column("resolved_at", dt, nullable=True),
    ], [("ix_care_uncertainties_person_status", ["person_id", "status"], False)])

    _create(bind, "care_missing_information", [
        sa.Column("person_id", s(36), sa.ForeignKey("persons.id", ondelete="CASCADE"), nullable=False),
        sa.Column("care_record_id", s(36), sa.ForeignKey("living_care_records.id", ondelete="CASCADE"), nullable=True),
        sa.Column("topic", s(160), nullable=False),
        sa.Column("description", txt, nullable=False),
        sa.Column("priority", s(24), nullable=False, server_default="normal"),
        sa.Column("status", s(32), nullable=False, server_default="open"),
        sa.Column("created_at", dt, nullable=False, server_default=now),
        sa.Column("resolved_at", dt, nullable=True),
    ], [("ix_care_missing_information_person_status", ["person_id", "status"], False)])

    _create(bind, "care_conflicts", [
        sa.Column("person_id", s(36), sa.ForeignKey("persons.id", ondelete="CASCADE"), nullable=False),
        sa.Column("care_record_id", s(36), sa.ForeignKey("living_care_records.id", ondelete="CASCADE"), nullable=True),
        sa.Column("conflict_type", s(80), nullable=False),
        sa.Column("status", s(32), nullable=False, server_default="open"),
        sa.Column("claim_refs", js, nullable=False, server_default=sa.text("'[]'")),
        sa.Column("description", txt, nullable=False),
        sa.Column("resolution", txt, nullable=True),
        sa.Column("resolved_by_caregiver_id", s(36), sa.ForeignKey("caregivers.id", ondelete="SET NULL")),
        sa.Column("created_at", dt, nullable=False, server_default=now),
        sa.Column("resolved_at", dt, nullable=True),
    ], [("ix_care_conflicts_person_status", ["person_id", "status"], False)])

    _create(bind, "care_action_outcomes", [
        sa.Column("person_id", s(36), sa.ForeignKey("persons.id", ondelete="CASCADE"), nullable=False),
        sa.Column("action_id", s(36), nullable=False),
        sa.Column("outcome_status", s(32), nullable=False, server_default="unknown"),
        sa.Column("description", txt, nullable=True),
        sa.Column("evidence_ids", js, nullable=False, server_default=sa.text("'[]'")),
        sa.Column("observed_at", dt, nullable=True),
        sa.Column("recorded_at", dt, nullable=False, server_default=now),
        sa.Column("recorded_by_caregiver_id", s(36), sa.ForeignKey("caregivers.id", ondelete="SET NULL")),
        sa.Column("provenance_id", s(36), sa.ForeignKey("record_provenance.id", ondelete="SET NULL")),
        sa.Column("created_at", dt, nullable=False, server_default=now),
    ], [("ix_care_action_outcomes_person_action", ["person_id", "action_id"], False)])

    # Verify critical relationships after creation. This is a safety check, not
    # a claim that application APIs are wired to these tables yet.
    inspector = inspect(bind)
    required = (
        "living_care_records", "care_record_versions", "care_record_snapshots",
        "record_provenance", "record_provenance_links", "care_record_change_sets",
        "care_record_change_items", "care_corrections", "record_amendments",
        "record_amendment_items", "care_record_audit_log", "care_uncertainties",
        "care_missing_information", "care_conflicts", "care_action_outcomes",
        "care_facts", "care_fact_evidence", "care_delta_runs", "care_delta_items",
        "care_events", "evidence", "evidence_uploads",
    )
    missing = [name for name in required if not inspector.has_table(name)]
    if missing:
        raise RuntimeError("LCR schema verification failed; missing: " + ", ".join(missing))


def downgrade():
    # Non-destructive by design: production LCR history must never be dropped
    # automatically during a rollback.
    pass
