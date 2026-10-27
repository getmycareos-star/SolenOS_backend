"""Add missing deep care-continuity relationships and decision/integrity records.

Existing evidence, events, changes, situations, temporal claims, uncertainty,
contradictions, state revisions, and Delta runs are reused. This migration
creates only domain structures that have no equivalent in the current schema.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "20261026_04_deep_care_continuity"
down_revision = "20261026_03_identity_security"
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

    # A care fact is a claim with an explicit epistemic status, not the source
    # document itself and not a machine-generated hypothesis.
    _create(bind, "care_facts", [
        sa.Column("person_id", s(36), sa.ForeignKey("persons.id", ondelete="CASCADE"), nullable=False),
        sa.Column("subject_type", s(80), nullable=False),
        sa.Column("subject_id", s(36), nullable=True),
        sa.Column("fact_type", s(100), nullable=False),
        sa.Column("fact_value", txt, nullable=False),
        sa.Column("epistemic_status", s(32), nullable=False, server_default="reported"),
        sa.Column("effective_at", dt, nullable=True),
        sa.Column("recorded_at", dt, nullable=False, server_default=NOW),
        sa.Column("superseded_by_fact_id", s(36), sa.ForeignKey("care_facts.id"), nullable=True),
        sa.Column("created_by_caregiver_id", s(36), sa.ForeignKey("caregivers.id", ondelete="SET NULL"), nullable=True),
        sa.Column("review_notes", txt, nullable=True),
        sa.Column("created_at", dt, nullable=False, server_default=NOW),
        sa.Column("updated_at", dt, nullable=True),
    ], [("ix_care_facts_person_subject", ["person_id", "subject_type", "subject_id"], False),
        ("ix_care_facts_person_status", ["person_id", "epistemic_status"], False)])

    _create(bind, "care_fact_evidence", [
        sa.Column("person_id", s(36), sa.ForeignKey("persons.id", ondelete="CASCADE"), nullable=False),
        sa.Column("fact_id", s(36), sa.ForeignKey("care_facts.id", ondelete="CASCADE"), nullable=False),
        sa.Column("evidence_id", s(36), sa.ForeignKey("evidence.id", ondelete="CASCADE"), nullable=False),
        sa.Column("evidence_role", s(24), nullable=False, server_default="supports"),
        sa.Column("source_span", txt, nullable=True),
        sa.Column("created_at", dt, nullable=False, server_default=NOW),
    ], [("uq_care_fact_evidence_link", ["fact_id", "evidence_id", "evidence_role"], True),
        ("ix_care_fact_evidence_person", ["person_id", "fact_id"], False)])

    _create(bind, "care_record_relationships", [
        sa.Column("person_id", s(36), sa.ForeignKey("persons.id", ondelete="CASCADE"), nullable=False),
        sa.Column("source_type", s(80), nullable=False),
        sa.Column("source_id", s(36), nullable=False),
        sa.Column("relationship_type", s(80), nullable=False),
        sa.Column("target_type", s(80), nullable=False),
        sa.Column("target_id", s(36), nullable=False),
        sa.Column("provenance", txt, nullable=True),
        sa.Column("created_by_caregiver_id", s(36), sa.ForeignKey("caregivers.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_at", dt, nullable=False, server_default=NOW),
    ], [("ix_care_record_relationships_person_source", ["person_id", "source_type", "source_id"], False),
        ("ix_care_record_relationships_person_target", ["person_id", "target_type", "target_id"], False)])

    # The legacy 20261023_04 Delta migration is not on every deployed
    # Alembic ancestry. Create its canonical table here when absent, before
    # adding care_delta_items' foreign key. Existing rows/tables are preserved.
    if not inspect(bind).has_table("care_delta_runs"):
        op.create_table(
            "care_delta_runs",
            sa.Column("id", sa.String(), primary_key=True, nullable=False),
            sa.Column("person_id", sa.String(), sa.ForeignKey("persons.id"), nullable=False),
            sa.Column("source", sa.String(), nullable=False),
            sa.Column("event_date", dt, nullable=False),
            sa.Column("source_event_id", sa.String(), sa.ForeignKey("care_events.id"), nullable=True),
            sa.Column("evidence_ids", js, nullable=False),
            sa.Column("delta_json", txt, nullable=False),
            sa.Column("model_name", sa.String(), nullable=False),
            sa.Column("created_by_caregiver_id", sa.String(), nullable=True),
            sa.Column("created_at", dt, nullable=False, server_default=NOW),
        )
    delta_indexes = {item["name"] for item in inspect(bind).get_indexes("care_delta_runs")}
    if "ix_care_delta_runs_person_id" not in delta_indexes:
        op.create_index("ix_care_delta_runs_person_id", "care_delta_runs", ["person_id"])
    if "ix_care_delta_runs_person_created" not in delta_indexes:
        op.create_index("ix_care_delta_runs_person_created", "care_delta_runs", ["person_id", "created_at"])

    _create(bind, "care_delta_items", [
        sa.Column("person_id", s(36), sa.ForeignKey("persons.id", ondelete="CASCADE"), nullable=False),
        sa.Column("delta_run_id", s(36), sa.ForeignKey("care_delta_runs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("item_type", s(80), nullable=False),
        sa.Column("target_type", s(80), nullable=True),
        sa.Column("target_id", s(36), nullable=True),
        sa.Column("before_state", txt, nullable=True),
        sa.Column("after_state", txt, nullable=True),
        sa.Column("evidence_ids", js, nullable=False, server_default=sa.text("'[]'")),
        sa.Column("reconciliation_status", s(32), nullable=False, server_default="pending_review"),
        sa.Column("review_notes", txt, nullable=True),
        sa.Column("reviewed_by_caregiver_id", s(36), sa.ForeignKey("caregivers.id", ondelete="SET NULL"), nullable=True),
        sa.Column("reviewed_at", dt, nullable=True),
        sa.Column("created_at", dt, nullable=False, server_default=NOW),
    ], [("ix_care_delta_items_person_run", ["person_id", "delta_run_id"], False),
        ("ix_care_delta_items_person_status", ["person_id", "reconciliation_status"], False)])

    _create(bind, "care_integrity_findings", [
        sa.Column("person_id", s(36), sa.ForeignKey("persons.id", ondelete="CASCADE"), nullable=False),
        sa.Column("finding_type", s(80), nullable=False),
        sa.Column("severity", s(24), nullable=False, server_default="warning"),
        sa.Column("status", s(32), nullable=False, server_default="open"),
        sa.Column("target_type", s(80), nullable=True),
        sa.Column("target_id", s(36), nullable=True),
        sa.Column("description", txt, nullable=False),
        sa.Column("evidence_ids", js, nullable=False, server_default=sa.text("'[]'")),
        sa.Column("detected_by", s(80), nullable=False, server_default="system_check"),
        sa.Column("detected_at", dt, nullable=False, server_default=NOW),
        sa.Column("created_at", dt, nullable=False, server_default=NOW),
    ], [("ix_care_integrity_findings_person_status", ["person_id", "status"], False)])

    _create(bind, "care_integrity_resolutions", [
        sa.Column("person_id", s(36), sa.ForeignKey("persons.id", ondelete="CASCADE"), nullable=False),
        sa.Column("finding_id", s(36), sa.ForeignKey("care_integrity_findings.id", ondelete="CASCADE"), nullable=False),
        sa.Column("resolution_status", s(32), nullable=False),
        sa.Column("resolution_notes", txt, nullable=True),
        sa.Column("evidence_ids", js, nullable=False, server_default=sa.text("'[]'")),
        sa.Column("resolved_by_caregiver_id", s(36), sa.ForeignKey("caregivers.id", ondelete="SET NULL"), nullable=True),
        sa.Column("resolved_at", dt, nullable=False, server_default=NOW),
        sa.Column("created_at", dt, nullable=False, server_default=NOW),
    ], [("ix_care_integrity_resolutions_person_finding", ["person_id", "finding_id"], False)])

    _create(bind, "care_decisions", [
        sa.Column("person_id", s(36), sa.ForeignKey("persons.id", ondelete="CASCADE"), nullable=False),
        sa.Column("title", s(240), nullable=False),
        sa.Column("decision_text", txt, nullable=False),
        sa.Column("decision_status", s(32), nullable=False, server_default="recorded"),
        sa.Column("decided_at", dt, nullable=True),
        sa.Column("recorded_at", dt, nullable=False, server_default=NOW),
        sa.Column("context", txt, nullable=True),
        sa.Column("evidence_ids", js, nullable=False, server_default=sa.text("'[]'")),
        sa.Column("created_by_caregiver_id", s(36), sa.ForeignKey("caregivers.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_at", dt, nullable=False, server_default=NOW),
    ], [("ix_care_decisions_person_decided", ["person_id", "decided_at"], False)])

    _create(bind, "decision_outcomes", [
        sa.Column("person_id", s(36), sa.ForeignKey("persons.id", ondelete="CASCADE"), nullable=False),
        sa.Column("decision_id", s(36), sa.ForeignKey("care_decisions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("outcome_status", s(32), nullable=False, server_default="unknown"),
        sa.Column("observed_at", dt, nullable=True),
        sa.Column("description", txt, nullable=True),
        sa.Column("evidence_ids", js, nullable=False, server_default=sa.text("'[]'")),
        sa.Column("is_intended_outcome", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_by_caregiver_id", s(36), sa.ForeignKey("caregivers.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_at", dt, nullable=False, server_default=NOW),
    ], [("ix_decision_outcomes_person_decision", ["person_id", "decision_id"], False)])

    _create(bind, "care_reconciliation_records", [
        sa.Column("person_id", s(36), sa.ForeignKey("persons.id", ondelete="CASCADE"), nullable=False),
        sa.Column("target_type", s(80), nullable=False),
        sa.Column("target_id", s(36), nullable=False),
        sa.Column("reconciliation_status", s(32), nullable=False, server_default="unresolved"),
        sa.Column("competing_claim_ids", js, nullable=False, server_default=sa.text("'[]'")),
        sa.Column("evidence_ids", js, nullable=False, server_default=sa.text("'[]'")),
        sa.Column("decision_notes", txt, nullable=True),
        sa.Column("reviewed_by_caregiver_id", s(36), sa.ForeignKey("caregivers.id", ondelete="SET NULL"), nullable=True),
        sa.Column("reviewed_at", dt, nullable=True),
        sa.Column("created_at", dt, nullable=False, server_default=NOW),
    ], [("ix_care_reconciliation_person_target", ["person_id", "target_type", "target_id"], False)])


def downgrade():
    bind = op.get_bind()
    # Data-bearing continuity tables are never silently dropped by downgrade.
    names = ["care_reconciliation_records", "decision_outcomes", "care_decisions",
             "care_integrity_resolutions", "care_integrity_findings", "care_delta_items",
             "care_record_relationships", "care_fact_evidence", "care_facts"]
    for name in names:
        if inspect(bind).has_table(name):
            count = bind.execute(sa.text(f'SELECT COUNT(*) FROM "{name}"')).scalar() or 0
            if count:
                raise RuntimeError(f"Refusing to drop {name}: it contains {count} records.")
    for name in names:
        if inspect(bind).has_table(name):
            op.drop_table(name)
