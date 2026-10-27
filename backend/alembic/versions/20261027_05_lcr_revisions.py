"""Add missing LCR identity, section, status, retraction, and revision history tables.

All writes are additive. Revision rows are append-only records of persisted changes;
the existing facts/events/evidence tables remain canonical for their source data.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "20261027_05_lcr_revisions"
down_revision = "20261027_04_caregiver_context"
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

    _create(bind, "care_record_identifiers", [
        sa.Column("person_id", s(36), sa.ForeignKey("persons.id", ondelete="CASCADE"), nullable=False),
        sa.Column("care_record_id", s(36), sa.ForeignKey("living_care_records.id", ondelete="CASCADE"), nullable=False),
        sa.Column("identifier_type", s(80), nullable=False),
        # Do not store raw external identifiers such as medical record numbers.
        sa.Column("identifier_fingerprint", s(128), nullable=False),
        sa.Column("masked_value", s(128), nullable=True),
        sa.Column("source_type", s(80), nullable=True),
        sa.Column("source_id", s(36), nullable=True),
        sa.Column("status", s(32), nullable=False, server_default="active"),
        sa.Column("valid_from", dt, nullable=True),
        sa.Column("valid_to", dt, nullable=True),
        sa.Column("created_at", dt, nullable=False, server_default=now),
        sa.Column("created_by_caregiver_id", s(36), sa.ForeignKey("caregivers.id", ondelete="SET NULL")),
    ], [
        ("uq_care_record_identifier_fingerprint", ["care_record_id", "identifier_type", "identifier_fingerprint"], True),
        ("ix_care_record_identifiers_person", ["person_id", "identifier_type"], False),
    ])

    _create(bind, "care_record_sections", [
        sa.Column("person_id", s(36), sa.ForeignKey("persons.id", ondelete="CASCADE"), nullable=False),
        sa.Column("care_record_id", s(36), sa.ForeignKey("living_care_records.id", ondelete="CASCADE"), nullable=False),
        sa.Column("section_key", s(100), nullable=False),
        sa.Column("section_payload", js, nullable=False),
        sa.Column("version_number", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("updated_at", dt, nullable=False, server_default=now),
        sa.Column("updated_by_caregiver_id", s(36), sa.ForeignKey("caregivers.id", ondelete="SET NULL")),
    ], [
        ("uq_care_record_section_key", ["care_record_id", "section_key"], True),
        ("ix_care_record_sections_person", ["person_id", "updated_at"], False),
    ])

    _create(bind, "care_record_links", [
        sa.Column("person_id", s(36), sa.ForeignKey("persons.id", ondelete="CASCADE"), nullable=False),
        sa.Column("source_record_id", s(36), sa.ForeignKey("living_care_records.id", ondelete="CASCADE"), nullable=False),
        sa.Column("target_record_id", s(36), sa.ForeignKey("living_care_records.id", ondelete="CASCADE"), nullable=False),
        sa.Column("relationship", s(60), nullable=False),
        sa.Column("created_at", dt, nullable=False, server_default=now),
        sa.Column("created_by_caregiver_id", s(36), sa.ForeignKey("caregivers.id", ondelete="SET NULL")),
    ], [
        ("uq_care_record_link", ["source_record_id", "target_record_id", "relationship"], True),
        ("ix_care_record_links_person", ["person_id", "source_record_id"], False),
    ])

    _create(bind, "care_record_status_history", [
        sa.Column("person_id", s(36), sa.ForeignKey("persons.id", ondelete="CASCADE"), nullable=False),
        sa.Column("care_record_id", s(36), sa.ForeignKey("living_care_records.id", ondelete="CASCADE"), nullable=False),
        sa.Column("previous_status", s(32), nullable=True),
        sa.Column("new_status", s(32), nullable=False),
        sa.Column("reason", txt, nullable=True),
        sa.Column("changed_by_caregiver_id", s(36), sa.ForeignKey("caregivers.id", ondelete="SET NULL")),
        sa.Column("provenance_id", s(36), sa.ForeignKey("record_provenance.id", ondelete="SET NULL")),
        sa.Column("changed_at", dt, nullable=False, server_default=now),
    ], [("ix_care_record_status_history_time", ["person_id", "care_record_id", "changed_at"], False)])

    revision_specs = [
        ("care_fact_revisions", "care_fact_id", "care_facts"),
        ("care_assertion_revisions", "assertion_id", "care_assertions"),
        ("care_decision_revisions", "decision_id", "care_decisions"),
        ("care_event_revisions", "event_id", "care_events"),
        ("care_observation_revisions", "observation_id", "caregiver_observations"),
        ("care_change_revisions", "change_id", "changes"),
        ("care_situation_revisions", "situation_id", "situations"),
        ("care_evidence_revisions", "evidence_id", "evidence"),
    ]
    for table_name, target_column, target_table in revision_specs:
        _create(bind, table_name, [
            sa.Column("person_id", s(36), sa.ForeignKey("persons.id", ondelete="CASCADE"), nullable=False),
            sa.Column(target_column, s(36), sa.ForeignKey(f"{target_table}.id", ondelete="CASCADE"), nullable=False),
            sa.Column("revision_number", sa.Integer(), nullable=False),
            sa.Column("previous_payload", js, nullable=True),
            sa.Column("current_payload", js, nullable=True),
            sa.Column("revision_reason", txt, nullable=True),
            sa.Column("provenance_id", s(36), sa.ForeignKey("record_provenance.id", ondelete="SET NULL")),
            sa.Column("changed_by_caregiver_id", s(36), sa.ForeignKey("caregivers.id", ondelete="SET NULL")),
            sa.Column("changed_at", dt, nullable=False, server_default=now),
        ], [
            (f"uq_{table_name}_revision", [target_column, "revision_number"], True),
            (f"ix_{table_name}_person_time", ["person_id", "changed_at"], False),
        ])

    _create(bind, "care_record_retractions", [
        sa.Column("person_id", s(36), sa.ForeignKey("persons.id", ondelete="CASCADE"), nullable=False),
        sa.Column("care_record_id", s(36), sa.ForeignKey("living_care_records.id", ondelete="CASCADE"), nullable=False),
        sa.Column("target_type", s(100), nullable=False),
        sa.Column("target_id", s(36), nullable=False),
        sa.Column("reason", txt, nullable=False),
        sa.Column("status", s(32), nullable=False, server_default="retracted"),
        sa.Column("provenance_id", s(36), sa.ForeignKey("record_provenance.id", ondelete="SET NULL")),
        sa.Column("retracted_by_caregiver_id", s(36), sa.ForeignKey("caregivers.id", ondelete="SET NULL")),
        sa.Column("retracted_at", dt, nullable=False, server_default=now),
    ], [("ix_care_record_retractions_target", ["person_id", "target_type", "target_id"], False)])

    _create(bind, "care_record_restorations", [
        sa.Column("person_id", s(36), sa.ForeignKey("persons.id", ondelete="CASCADE"), nullable=False),
        sa.Column("care_record_id", s(36), sa.ForeignKey("living_care_records.id", ondelete="CASCADE"), nullable=False),
        sa.Column("retraction_id", s(36), sa.ForeignKey("care_record_retractions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("reason", txt, nullable=False),
        sa.Column("provenance_id", s(36), sa.ForeignKey("record_provenance.id", ondelete="SET NULL")),
        sa.Column("restored_by_caregiver_id", s(36), sa.ForeignKey("caregivers.id", ondelete="SET NULL")),
        sa.Column("restored_at", dt, nullable=False, server_default=now),
    ], [("ix_care_record_restorations_time", ["person_id", "care_record_id", "restored_at"], False)])


def downgrade():
    # Preserve continuity history during rollback; this migration is additive.
    pass
