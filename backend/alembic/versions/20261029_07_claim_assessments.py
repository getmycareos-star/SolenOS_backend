"""Add versioned, evidence-linked claim assessments.

Revision ID: 20261029_07_claim_assessments
Revises: 20261029_06_observation_coverage
"""
from alembic import op
import sqlalchemy as sa

revision = "20261029_07_claim_assessments"
down_revision = "20261029_06_observation_coverage"
branch_labels = None
depends_on = None


def upgrade() -> None:
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
    op.create_index(
        "ix_evidence_claim_assessments_person_claim",
        "evidence_claim_assessments",
        ["person_id", "claim_id", "claim_version"],
    )
    op.create_index(
        "ix_evidence_claim_assessments_person_created",
        "evidence_claim_assessments",
        ["person_id", "created_at"],
    )

    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute("""
            CREATE OR REPLACE FUNCTION solenos_prevent_claim_assessment_mutation()
            RETURNS trigger LANGUAGE plpgsql
            AS 'BEGIN
              RAISE EXCEPTION ''Evidence claim assessments are append-only; create a new version instead.'';
            END;'
        """)
        op.execute("""
            CREATE TRIGGER trg_evidence_claim_assessments_immutable
            BEFORE UPDATE OR DELETE ON evidence_claim_assessments
            FOR EACH ROW EXECUTE FUNCTION solenos_prevent_claim_assessment_mutation()
        """)
    elif bind.dialect.name == "sqlite":
        op.execute("""
            CREATE TRIGGER trg_evidence_claim_assessments_no_update
            BEFORE UPDATE ON evidence_claim_assessments
            BEGIN
              SELECT RAISE(ABORT, 'Evidence claim assessments are append-only; create a new version instead.');
            END
        """)
        op.execute("""
            CREATE TRIGGER trg_evidence_claim_assessments_no_delete
            BEFORE DELETE ON evidence_claim_assessments
            BEGIN
              SELECT RAISE(ABORT, 'Evidence claim assessments are append-only; create a new version instead.');
            END
        """)
    else:
        raise RuntimeError(f"Claim-assessment immutability is unsupported for database dialect {bind.dialect.name!r}")


def downgrade() -> None:
    raise RuntimeError("Refusing to drop append-only claim assessment history.")
