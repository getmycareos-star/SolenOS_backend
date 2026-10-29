"""Persist explicit domain-specific freshness policies and assessments.

Revision ID: 20261029_25_evidence_freshness_policy
Revises: 20261029_24_baseline_history
"""
from alembic import op
import sqlalchemy as sa

revision = "20261029_25_evidence_freshness_policy"
down_revision = "20261029_24_baseline_history"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "evidence_freshness_policies",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("person_id", sa.String(), sa.ForeignKey("persons.id", ondelete="CASCADE"), nullable=False),
        sa.Column("domain_key", sa.String(length=80), nullable=False),
        sa.Column("policy_version", sa.String(length=64), nullable=False),
        sa.Column("max_age_days", sa.Integer(), nullable=True),
        sa.Column("historical_after_days", sa.Integer(), nullable=True),
        sa.Column("rationale", sa.Text(), nullable=False),
        sa.Column("created_by_caregiver_id", sa.String(), sa.ForeignKey("caregivers.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("person_id", "domain_key", "policy_version", name="uq_evidence_freshness_policy_version"),
    )
    op.create_index("ix_evidence_freshness_policies_person_id", "evidence_freshness_policies", ["person_id"])
    op.create_index("ix_evidence_freshness_policies_person_domain", "evidence_freshness_policies", ["person_id", "domain_key"])
    op.create_table(
        "evidence_freshness_assessments",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("person_id", sa.String(), sa.ForeignKey("persons.id", ondelete="CASCADE"), nullable=False),
        sa.Column("evidence_id", sa.String(), sa.ForeignKey("evidence.id", ondelete="CASCADE"), nullable=False),
        sa.Column("policy_id", sa.String(), sa.ForeignKey("evidence_freshness_policies.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("policy_version", sa.String(length=64), nullable=False),
        sa.Column("freshness_status", sa.String(length=32), nullable=False),
        sa.Column("source_time", sa.DateTime(timezone=True), nullable=True),
        sa.Column("assessed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("age_days", sa.Integer(), nullable=True),
        sa.Column("rationale", sa.Text(), nullable=False),
        sa.Column("assessed_by_caregiver_id", sa.String(), sa.ForeignKey("caregivers.id"), nullable=False),
    )
    op.create_index("ix_evidence_freshness_assessments_person_id", "evidence_freshness_assessments", ["person_id"])
    op.create_index("ix_evidence_freshness_assessments_person_evidence", "evidence_freshness_assessments", ["person_id", "evidence_id", "assessed_at"])
    op.create_index("ix_evidence_freshness_assessments_policy", "evidence_freshness_assessments", ["policy_id"])

    if op.get_bind().dialect.name == "postgresql":
        op.execute("""
        CREATE FUNCTION prevent_evidence_freshness_mutation() RETURNS trigger AS $$
        BEGIN
          RAISE EXCEPTION 'evidence freshness policies and assessments are append-only';
        END;
        $$ LANGUAGE plpgsql;
        """)
        for table in ("evidence_freshness_policies", "evidence_freshness_assessments"):
            op.execute(
                f"CREATE TRIGGER trg_{table}_immutable BEFORE UPDATE OR DELETE ON {table} "
                "FOR EACH ROW EXECUTE FUNCTION prevent_evidence_freshness_mutation();"
            )


def downgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        for table in ("evidence_freshness_assessments", "evidence_freshness_policies"):
            op.execute(f"DROP TRIGGER IF EXISTS trg_{table}_immutable ON {table};")
        op.execute("DROP FUNCTION IF EXISTS prevent_evidence_freshness_mutation();")
    op.drop_table("evidence_freshness_assessments")
    op.drop_table("evidence_freshness_policies")
