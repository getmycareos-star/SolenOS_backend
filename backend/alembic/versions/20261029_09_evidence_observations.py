"""Persist source-span-bound structured observations.

Revision ID: 20261029_09_obs
Revises: 20261029_08_source_links
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "20261029_09_obs"
down_revision = "20261029_08_source_links"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    required = {"persons", "evidence", "caregivers"}
    missing = required - set(inspector.get_table_names())
    if missing:
        raise RuntimeError(f"Required observation source tables are missing: {sorted(missing)}")
    if not inspector.has_table("evidence_observations"):
        op.create_table(
            "evidence_observations",
            sa.Column("id", sa.String(), primary_key=True),
            sa.Column("person_id", sa.String(), sa.ForeignKey("persons.id", ondelete="CASCADE"), nullable=False),
            sa.Column("evidence_id", sa.String(), sa.ForeignKey("evidence.id", ondelete="RESTRICT"), nullable=False),
            sa.Column("observation_kind", sa.String(length=40), nullable=False),
            sa.Column("epistemic_status", sa.String(length=24), nullable=False),
            sa.Column("subject_label", sa.String(length=160), nullable=False),
            sa.Column("attribute", sa.String(length=160), nullable=False),
            sa.Column("value_text", sa.Text(), nullable=True),
            sa.Column("normalized_value", sa.Text(), nullable=True),
            sa.Column("units", sa.String(length=80), nullable=True),
            sa.Column("context", sa.Text(), nullable=True),
            sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("observed_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("reported_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("observer_label", sa.Text(), nullable=True),
            sa.Column("observer_caregiver_id", sa.String(), sa.ForeignKey("caregivers.id"), nullable=True),
            sa.Column("observation_method", sa.String(length=120), nullable=True),
            sa.Column("source_passage", sa.Text(), nullable=False),
            sa.Column("source_span_start", sa.Integer(), nullable=False),
            sa.Column("source_span_end", sa.Integer(), nullable=False),
            sa.Column("negated", sa.Boolean(), nullable=True),
            sa.Column("hedge_marker", sa.String(length=120), nullable=True),
            sa.Column("ambiguity_note", sa.Text(), nullable=True),
            sa.Column("extraction_method", sa.String(length=80), nullable=False),
            sa.Column("extraction_version", sa.String(length=80), nullable=False),
            sa.Column("extraction_run_id", sa.String(length=128), nullable=True),
            sa.Column("created_by_caregiver_id", sa.String(), sa.ForeignKey("caregivers.id"), nullable=False),
            sa.Column("recorded_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.UniqueConstraint(
                "person_id", "evidence_id", "source_span_start", "source_span_end",
                "attribute", "observation_kind", "epistemic_status",
                name="uq_evidence_observation_source_span",
            ),
        )
    else:
        columns = {c["name"] for c in inspect(bind).get_columns("evidence_observations")}
        expected = {
            "id", "person_id", "evidence_id", "observation_kind", "epistemic_status",
            "subject_label", "attribute", "value_text", "normalized_value", "units", "context",
            "occurred_at", "observed_at", "reported_at", "observer_label", "observer_caregiver_id",
            "observation_method", "source_passage", "source_span_start", "source_span_end", "negated",
            "hedge_marker", "ambiguity_note", "extraction_method", "extraction_version",
            "extraction_run_id", "created_by_caregiver_id", "recorded_at",
        }
        if expected - columns:
            raise RuntimeError(f"Existing evidence_observations table is incomplete: {sorted(expected - columns)}")
    indexes = {i["name"] for i in inspect(bind).get_indexes("evidence_observations")}
    for name, columns in (
        ("ix_evidence_observations_person_subject", ["person_id", "subject_label", "attribute"]),
        ("ix_evidence_observations_person_time", ["person_id", "occurred_at", "recorded_at"]),
    ):
        if name not in indexes:
            op.create_index(name, "evidence_observations", columns)
    if bind.dialect.name == "postgresql":
        op.execute("""
            CREATE OR REPLACE FUNCTION solenos_prevent_evidence_observation_mutation()
            RETURNS trigger LANGUAGE plpgsql
            AS 'BEGIN
              RAISE EXCEPTION ''Evidence observations are append-only; create a corrected observation instead.'';
            END;'
        """)
        op.execute("""
            DO $trigger$
            BEGIN
              IF NOT EXISTS (
                SELECT 1 FROM pg_trigger
                WHERE tgname = 'trg_evidence_observations_immutable' AND NOT tgisinternal
              ) THEN
                CREATE TRIGGER trg_evidence_observations_immutable
                BEFORE UPDATE OR DELETE ON evidence_observations
                FOR EACH ROW EXECUTE FUNCTION solenos_prevent_evidence_observation_mutation();
              END IF;
            END;
            $trigger$;
        """)
    elif bind.dialect.name == "sqlite":
        op.execute("""
            CREATE TRIGGER IF NOT EXISTS trg_evidence_observations_no_update
            BEFORE UPDATE ON evidence_observations
            BEGIN
              SELECT RAISE(ABORT, 'Evidence observations are append-only.');
            END
        """)
        op.execute("""
            CREATE TRIGGER IF NOT EXISTS trg_evidence_observations_no_delete
            BEFORE DELETE ON evidence_observations
            BEGIN
              SELECT RAISE(ABORT, 'Evidence observations are append-only.');
            END
        """)
    else:
        raise RuntimeError(f"Observation immutability is unsupported for database dialect {bind.dialect.name!r}")


def downgrade() -> None:
    raise RuntimeError("Refusing to remove source-span-bound evidence observation history.")
