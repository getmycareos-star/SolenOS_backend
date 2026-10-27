"""Add distinct caregiver-context and attribution records without duplicating existing tables.

This migration only creates a table when that exact table is absent. Existing care
events, evidence, observations, caregiver profiles/roles, and LCR records remain
the source of truth for their existing concepts.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "20261027_04_caregiver_context"
down_revision = "20261027_03_lcr_history"
branch_labels = None
depends_on = None


def _create_if_missing(bind, name, columns, indexes=()):
    inspector = inspect(bind)
    if inspector.has_table(name):
        return
    op.create_table(
        name,
        sa.Column("id", sa.String(36), primary_key=True, nullable=False),
        *columns,
    )
    for index_name, index_columns in indexes:
        op.create_index(index_name, name, index_columns, unique=False)


def upgrade():
    bind = op.get_bind()
    s, txt, dt, js = sa.String, sa.Text, sa.DateTime(timezone=True), sa.JSON
    now = sa.func.now()

    # Context is a caregiver's situated account, not an asserted clinical fact.
    _create_if_missing(bind, "caregiver_contexts", [
        sa.Column("person_id", s(36), sa.ForeignKey("persons.id", ondelete="CASCADE"), nullable=False),
        sa.Column("caregiver_id", s(36), sa.ForeignKey("caregivers.id", ondelete="CASCADE"), nullable=False),
        sa.Column("context_type", s(100), nullable=False),
        sa.Column("context_payload", js, nullable=False),
        sa.Column("epistemic_status", s(32), nullable=False, server_default="reported"),
        sa.Column("source_type", s(80), nullable=True),
        sa.Column("source_id", s(36), nullable=True),
        sa.Column("recorded_at", dt, nullable=False, server_default=now),
        sa.Column("effective_from", dt, nullable=True),
        sa.Column("effective_to", dt, nullable=True),
        sa.Column("notes", txt, nullable=True),
        sa.Column("created_at", dt, nullable=False, server_default=now),
        sa.Column("updated_at", dt, nullable=False, server_default=now),
    ], [
        ("ix_caregiver_contexts_person_time", ["person_id", "recorded_at"]),
        ("ix_caregiver_contexts_caregiver", ["caregiver_id", "context_type"]),
    ])

    # Perspectives preserve who reported an interpretation and its uncertainty.
    _create_if_missing(bind, "caregiver_perspectives", [
        sa.Column("person_id", s(36), sa.ForeignKey("persons.id", ondelete="CASCADE"), nullable=False),
        sa.Column("caregiver_id", s(36), sa.ForeignKey("caregivers.id", ondelete="CASCADE"), nullable=False),
        sa.Column("topic", s(160), nullable=False),
        sa.Column("perspective_text", txt, nullable=False),
        sa.Column("epistemic_status", s(32), nullable=False, server_default="reported"),
        sa.Column("evidence_ids", js, nullable=False, server_default=sa.text("'[]'")),
        sa.Column("source_type", s(80), nullable=True),
        sa.Column("source_id", s(36), nullable=True),
        sa.Column("recorded_at", dt, nullable=False, server_default=now),
        sa.Column("effective_from", dt, nullable=True),
        sa.Column("effective_to", dt, nullable=True),
        sa.Column("supersedes_id", s(36), nullable=True),
        sa.Column("created_at", dt, nullable=False, server_default=now),
    ], [
        ("ix_caregiver_perspectives_person_time", ["person_id", "recorded_at"]),
        ("ix_caregiver_perspectives_caregiver_topic", ["caregiver_id", "topic"]),
    ])

    # Caregiver-attributed observations are not automatically promoted to facts.
    _create_if_missing(bind, "caregiver_observations", [
        sa.Column("person_id", s(36), sa.ForeignKey("persons.id", ondelete="CASCADE"), nullable=False),
        sa.Column("caregiver_id", s(36), sa.ForeignKey("caregivers.id", ondelete="CASCADE"), nullable=False),
        sa.Column("observation_type", s(100), nullable=False),
        sa.Column("description", txt, nullable=False),
        sa.Column("observed_value", js, nullable=True),
        sa.Column("observed_at", dt, nullable=True),
        sa.Column("recorded_at", dt, nullable=False, server_default=now),
        sa.Column("epistemic_status", s(32), nullable=False, server_default="reported"),
        sa.Column("evidence_ids", js, nullable=False, server_default=sa.text("'[]'")),
        sa.Column("source_type", s(80), nullable=True),
        sa.Column("source_id", s(36), nullable=True),
        sa.Column("created_at", dt, nullable=False, server_default=now),
    ], [
        ("ix_caregiver_observations_person_time", ["person_id", "recorded_at"]),
        ("ix_caregiver_observations_caregiver_type", ["caregiver_id", "observation_type"]),
    ])

    inspector = inspect(bind)
    required = ("persons", "caregivers", "caregiver_contexts", "caregiver_perspectives", "caregiver_observations")
    missing = [name for name in required if not inspector.has_table(name)]
    if missing:
        raise RuntimeError("Caregiver-context schema verification failed; missing: " + ", ".join(missing))

    # Older SolenOS revisions may already have these tables with a different
    # contract. Extend them additively, preserving rows. Nullable new columns
    # avoid fabricating values for historical records; API writes must validate.
    additive_columns = {
        "caregiver_contexts": {
            "person_id": sa.Column("person_id", s(36), nullable=True),
            "caregiver_id": sa.Column("caregiver_id", s(36), nullable=True),
            "context_type": sa.Column("context_type", s(100), nullable=True),
            "context_payload": sa.Column("context_payload", js, nullable=True),
            "epistemic_status": sa.Column("epistemic_status", s(32), nullable=True, server_default="reported"),
            "source_type": sa.Column("source_type", s(80), nullable=True),
            "source_id": sa.Column("source_id", s(36), nullable=True),
            "recorded_at": sa.Column("recorded_at", dt, nullable=True, server_default=now),
            "effective_from": sa.Column("effective_from", dt, nullable=True),
            "effective_to": sa.Column("effective_to", dt, nullable=True),
            "notes": sa.Column("notes", txt, nullable=True),
            "created_at": sa.Column("created_at", dt, nullable=True, server_default=now),
            "updated_at": sa.Column("updated_at", dt, nullable=True, server_default=now),
        },
        "caregiver_perspectives": {
            "person_id": sa.Column("person_id", s(36), nullable=True),
            "caregiver_id": sa.Column("caregiver_id", s(36), nullable=True),
            "topic": sa.Column("topic", s(160), nullable=True),
            "perspective_text": sa.Column("perspective_text", txt, nullable=True),
            "epistemic_status": sa.Column("epistemic_status", s(32), nullable=True, server_default="reported"),
            "evidence_ids": sa.Column("evidence_ids", js, nullable=True, server_default=sa.text("'[]'")),
            "source_type": sa.Column("source_type", s(80), nullable=True),
            "source_id": sa.Column("source_id", s(36), nullable=True),
            "recorded_at": sa.Column("recorded_at", dt, nullable=True, server_default=now),
            "effective_from": sa.Column("effective_from", dt, nullable=True),
            "effective_to": sa.Column("effective_to", dt, nullable=True),
            "supersedes_id": sa.Column("supersedes_id", s(36), nullable=True),
            "created_at": sa.Column("created_at", dt, nullable=True, server_default=now),
        },
        "caregiver_observations": {
            "person_id": sa.Column("person_id", s(36), nullable=True),
            "caregiver_id": sa.Column("caregiver_id", s(36), nullable=True),
            "observation_type": sa.Column("observation_type", s(100), nullable=True),
            "description": sa.Column("description", txt, nullable=True),
            "observed_value": sa.Column("observed_value", js, nullable=True),
            "observed_at": sa.Column("observed_at", dt, nullable=True),
            "recorded_at": sa.Column("recorded_at", dt, nullable=True, server_default=now),
            "epistemic_status": sa.Column("epistemic_status", s(32), nullable=True, server_default="reported"),
            "evidence_ids": sa.Column("evidence_ids", js, nullable=True, server_default=sa.text("'[]'")),
            "source_type": sa.Column("source_type", s(80), nullable=True),
            "source_id": sa.Column("source_id", s(36), nullable=True),
            "created_at": sa.Column("created_at", dt, nullable=True, server_default=now),
        },
    }
    for table, additions in additive_columns.items():
        actual = {column["name"] for column in inspect(bind).get_columns(table)}
        for name, column in additions.items():
            if name not in actual:
                op.add_column(table, column)
                actual.add(name)

    # Verify the final schema shape without backfilling fabricated history.
    inspector = inspect(bind)
    for table, expected in {
        "caregiver_contexts": {"person_id", "caregiver_id", "context_type", "context_payload", "epistemic_status", "recorded_at"},
        "caregiver_perspectives": {"person_id", "caregiver_id", "topic", "perspective_text", "epistemic_status", "recorded_at"},
        "caregiver_observations": {"person_id", "caregiver_id", "observation_type", "description", "epistemic_status", "recorded_at"},
    }.items():
        actual = {column["name"] for column in inspector.get_columns(table)}
        missing_columns = sorted(expected - actual)
        if missing_columns:
            raise RuntimeError(f"{table} schema verification failed after additive reconciliation: {', '.join(missing_columns)}")


def downgrade():
    # Preserve caregiver history on rollback; production records must not be dropped.
    pass
