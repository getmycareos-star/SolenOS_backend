"""Persist append-only source lineage and corroboration assessments.

Revision ID: 20261029_08_evidence_source_relationships
Revises: 20261029_07_claim_assessments
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "20261029_08_evidence_source_relationships"
down_revision = "20261029_07_claim_assessments"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    required = {"persons", "evidence", "caregivers"}
    missing = required - set(inspector.get_table_names())
    if missing:
        raise RuntimeError(f"Required evidence lineage tables are missing: {sorted(missing)}")

    if not inspector.has_table("evidence_source_relationships"):
        op.create_table(
            "evidence_source_relationships",
            sa.Column("id", sa.String(), primary_key=True),
            sa.Column("person_id", sa.String(), sa.ForeignKey("persons.id", ondelete="CASCADE"), nullable=False),
            sa.Column("source_evidence_id", sa.String(), sa.ForeignKey("evidence.id", ondelete="CASCADE"), nullable=False),
            sa.Column("related_evidence_id", sa.String(), sa.ForeignKey("evidence.id", ondelete="CASCADE"), nullable=False),
            sa.Column("relationship_type", sa.String(length=32), nullable=False),
            sa.Column("source_independence_status", sa.String(length=24), nullable=False),
            sa.Column("origin_group_key", sa.String(length=128), nullable=True),
            sa.Column("rationale", sa.Text(), nullable=False),
            sa.Column("reviewed_by_caregiver_id", sa.String(), sa.ForeignKey("caregivers.id"), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.UniqueConstraint(
                "person_id", "source_evidence_id", "related_evidence_id", "relationship_type",
                name="uq_evidence_source_relationship",
            ),
        )
    else:
        columns = {c["name"] for c in inspect(bind).get_columns("evidence_source_relationships")}
        expected = {
            "id", "person_id", "source_evidence_id", "related_evidence_id",
            "relationship_type", "source_independence_status", "origin_group_key",
            "rationale", "reviewed_by_caregiver_id", "created_at",
        }
        if expected - columns:
            raise RuntimeError(
                "Existing evidence_source_relationships schema is incomplete; "
                f"missing columns: {sorted(expected - columns)}"
            )

    indexes = {i["name"] for i in inspect(bind).get_indexes("evidence_source_relationships")}
    for name, cols in (
        ("ix_evidence_source_relationships_person_source", ["person_id", "source_evidence_id"]),
        ("ix_evidence_source_relationships_person_related", ["person_id", "related_evidence_id"]),
    ):
        if name not in indexes:
            op.create_index(name, "evidence_source_relationships", cols)

    if bind.dialect.name == "postgresql":
        op.execute("""
            CREATE OR REPLACE FUNCTION solenos_prevent_evidence_source_relationship_mutation()
            RETURNS trigger LANGUAGE plpgsql
            AS 'BEGIN
              RAISE EXCEPTION ''Evidence source relationships are append-only; create a new assessment instead.'';
            END;'
        """)
        op.execute("""
            DO $trigger$
            BEGIN
              IF NOT EXISTS (
                SELECT 1 FROM pg_trigger
                WHERE tgname = 'trg_evidence_source_relationships_immutable'
                  AND NOT tgisinternal
              ) THEN
                CREATE TRIGGER trg_evidence_source_relationships_immutable
                BEFORE UPDATE OR DELETE ON evidence_source_relationships
                FOR EACH ROW EXECUTE FUNCTION solenos_prevent_evidence_source_relationship_mutation();
              END IF;
            END;
            $trigger$;
        """)
    elif bind.dialect.name == "sqlite":
        op.execute("""
            CREATE TRIGGER IF NOT EXISTS trg_evidence_source_relationships_no_update
            BEFORE UPDATE ON evidence_source_relationships
            BEGIN
              SELECT RAISE(ABORT, 'Evidence source relationships are append-only.');
            END
        """)
        op.execute("""
            CREATE TRIGGER IF NOT EXISTS trg_evidence_source_relationships_no_delete
            BEFORE DELETE ON evidence_source_relationships
            BEGIN
              SELECT RAISE(ABORT, 'Evidence source relationships are append-only.');
            END
        """)
    else:
        raise RuntimeError(
            f"Source relationship immutability is unsupported for database dialect {bind.dialect.name!r}"
        )


def downgrade() -> None:
    raise RuntimeError("Refusing to remove append-only evidence source lineage and corroboration history.")
