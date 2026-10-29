"""Add versioned, evidence-linked claim assessments.

Revision ID: 20261029_07_claim_assessments
Revises: 20261029_06_observation_coverage
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "20261029_07_claim_assessments"
down_revision = "20261029_06_observation_coverage"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    required_columns = {
        "id", "person_id", "claim_id", "claim_version", "status", "rationale",
        "policy_version", "evidence_links", "reviewed_by_caregiver_id",
        "supersedes_assessment_id", "created_at",
    }

    if not inspector.has_table("evidence_claim_assessments"):
        op.create_table(
            "evidence_claim_assessments",
            sa.Column("id", sa.String(), primary_key=True),
            sa.Column("person_id", sa.String(), sa.ForeignKey("persons.id"), nullable=False),
            sa.Column("claim_id", sa.String(), sa.ForeignKey("care_assertions.id"), nullable=False),
            sa.Column("claim_version", sa.Integer(), nullable=False),
            sa.Column("status", sa.String(length=32), nullable=False),
            sa.Column("rationale", sa.Text(), nullable=False),
            sa.Column("policy_version", sa.String(length=64), nullable=False),
            sa.Column("evidence_links", sa.JSON(), nullable=False),
            sa.Column("reviewed_by_caregiver_id", sa.String(), sa.ForeignKey("caregivers.id"), nullable=False),
            sa.Column("supersedes_assessment_id", sa.String(), sa.ForeignKey("evidence_claim_assessments.id"), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.UniqueConstraint("claim_id", "claim_version", name="uq_evidence_claim_assessment_version"),
        )
        inspector = inspect(bind)
    else:
        actual_columns = {column["name"] for column in inspector.get_columns("evidence_claim_assessments")}
        missing_columns = required_columns - actual_columns
        if missing_columns:
            raise RuntimeError(
                "Existing evidence_claim_assessments table is incomplete; refusing to accept an incompatible schema. "
                f"Missing columns: {sorted(missing_columns)}"
            )
        unique_constraints = inspector.get_unique_constraints("evidence_claim_assessments")
        unique_indexes = inspector.get_indexes("evidence_claim_assessments")
        has_version_uniqueness = (
            any(set(item.get("column_names") or []) == {"claim_id", "claim_version"} for item in unique_constraints)
            or any(item.get("unique") and set(item.get("column_names") or []) == {"claim_id", "claim_version"} for item in unique_indexes)
        )
        if not has_version_uniqueness:
            raise RuntimeError("Existing evidence_claim_assessments table lacks unique claim-version enforcement")

    indexes = {item["name"] for item in inspect(bind).get_indexes("evidence_claim_assessments")}
    for index_name, columns in (
        ("ix_evidence_claim_assessments_person_claim", ["person_id", "claim_id", "claim_version"]),
        ("ix_evidence_claim_assessments_person_created", ["person_id", "created_at"]),
    ):
        if index_name not in indexes:
            op.create_index(index_name, "evidence_claim_assessments", columns)

    if bind.dialect.name == "postgresql":
        op.execute("""
            CREATE OR REPLACE FUNCTION solenos_prevent_claim_assessment_mutation()
            RETURNS trigger LANGUAGE plpgsql
            AS 'BEGIN
              RAISE EXCEPTION ''Evidence claim assessments are append-only; create a new version instead.'';
            END;'
        """)
        op.execute("""
            DO $trigger$
            BEGIN
              IF NOT EXISTS (
                SELECT 1 FROM pg_trigger
                WHERE tgname = 'trg_evidence_claim_assessments_immutable'
                  AND NOT tgisinternal
              ) THEN
                CREATE TRIGGER trg_evidence_claim_assessments_immutable
                BEFORE UPDATE OR DELETE ON evidence_claim_assessments
                FOR EACH ROW EXECUTE FUNCTION solenos_prevent_claim_assessment_mutation();
              END IF;
            END;
            $trigger$;
        """)
    elif bind.dialect.name == "sqlite":
        op.execute("""
            CREATE TRIGGER IF NOT EXISTS trg_evidence_claim_assessments_no_update
            BEFORE UPDATE ON evidence_claim_assessments
            BEGIN
              SELECT RAISE(ABORT, 'Evidence claim assessments are append-only; create a new version instead.');
            END
        """)
        op.execute("""
            CREATE TRIGGER IF NOT EXISTS trg_evidence_claim_assessments_no_delete
            BEFORE DELETE ON evidence_claim_assessments
            BEGIN
              SELECT RAISE(ABORT, 'Evidence claim assessments are append-only; create a new version instead.');
            END
        """)
    else:
        raise RuntimeError(f"Claim-assessment immutability is unsupported for database dialect {bind.dialect.name!r}")


def downgrade() -> None:
    raise RuntimeError("Refusing to drop append-only claim assessment history.")
