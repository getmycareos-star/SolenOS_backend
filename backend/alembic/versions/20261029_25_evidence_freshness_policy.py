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
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    policy_columns = {
        "id", "person_id", "domain_key", "policy_version", "max_age_days",
        "historical_after_days", "rationale", "created_by_caregiver_id", "created_at",
    }
    assessment_columns = {
        "id", "person_id", "evidence_id", "policy_id", "policy_version",
        "freshness_status", "source_time", "assessed_at", "age_days", "rationale",
        "assessed_by_caregiver_id",
    }

    # Some legacy/schema-reconciliation paths pre-create ORM metadata before
    # Alembic reaches this revision. Reconcile safely rather than failing with
    # DuplicateTable or treating an incomplete pre-created schema as valid.
    if not inspector.has_table("evidence_freshness_policies"):
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
        inspector = sa.inspect(bind)
    else:
        actual = {column["name"] for column in inspector.get_columns("evidence_freshness_policies")}
        if policy_columns - actual:
            raise RuntimeError(
                "Pre-existing evidence_freshness_policies table is incomplete; "
                f"missing columns: {sorted(policy_columns - actual)}"
            )
        constraints = inspector.get_unique_constraints("evidence_freshness_policies")
        indexes = inspector.get_indexes("evidence_freshness_policies")
        has_version_unique = (
            any(item.get("name") == "uq_evidence_freshness_policy_version"
                and item.get("column_names") == ["person_id", "domain_key", "policy_version"] for item in constraints)
            or any(item.get("unique") and item.get("column_names") == ["person_id", "domain_key", "policy_version"] for item in indexes)
        )
        if not has_version_unique:
            raise RuntimeError("Pre-existing freshness policy table lacks unique person/domain/version enforcement")

    if not inspector.has_table("evidence_freshness_assessments"):
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
        inspector = sa.inspect(bind)
    else:
        actual = {column["name"] for column in inspector.get_columns("evidence_freshness_assessments")}
        if assessment_columns - actual:
            raise RuntimeError(
                "Pre-existing evidence_freshness_assessments table is incomplete; "
                f"missing columns: {sorted(assessment_columns - actual)}"
            )

    indexes = {item["name"] for item in sa.inspect(bind).get_indexes("evidence_freshness_policies")}
    if "ix_evidence_freshness_policies_person_id" not in indexes:
        op.create_index("ix_evidence_freshness_policies_person_id", "evidence_freshness_policies", ["person_id"])
    if "ix_evidence_freshness_policies_person_domain" not in indexes:
        op.create_index("ix_evidence_freshness_policies_person_domain", "evidence_freshness_policies", ["person_id", "domain_key"])
    indexes = {item["name"] for item in sa.inspect(bind).get_indexes("evidence_freshness_assessments")}
    if "ix_evidence_freshness_assessments_person_id" not in indexes:
        op.create_index("ix_evidence_freshness_assessments_person_id", "evidence_freshness_assessments", ["person_id"])
    if "ix_evidence_freshness_assessments_person_evidence" not in indexes:
        op.create_index("ix_evidence_freshness_assessments_person_evidence", "evidence_freshness_assessments", ["person_id", "evidence_id", "assessed_at"])
    if "ix_evidence_freshness_assessments_policy" not in indexes:
        op.create_index("ix_evidence_freshness_assessments_policy", "evidence_freshness_assessments", ["policy_id"])

    if bind.dialect.name == "postgresql":
        op.execute("""
        CREATE OR REPLACE FUNCTION prevent_evidence_freshness_mutation() RETURNS trigger AS $$
        BEGIN
          RAISE EXCEPTION 'evidence freshness policies and assessments are append-only';
        END;
        $$ LANGUAGE plpgsql;
        """)
        for table in ("evidence_freshness_policies", "evidence_freshness_assessments"):
            op.execute(f"""
            DO $trigger$
            BEGIN
              IF NOT EXISTS (
                SELECT 1 FROM pg_trigger
                WHERE tgname = 'trg_{table}_immutable' AND NOT tgisinternal
              ) THEN
                CREATE TRIGGER trg_{table}_immutable
                BEFORE UPDATE OR DELETE ON {table}
                FOR EACH ROW EXECUTE FUNCTION prevent_evidence_freshness_mutation();
              END IF;
            END;
            $trigger$;
            """)
    elif bind.dialect.name == "sqlite":
        for table in ("evidence_freshness_policies", "evidence_freshness_assessments"):
            op.execute(f"""
            CREATE TRIGGER IF NOT EXISTS trg_{table}_no_update
            BEFORE UPDATE ON {table}
            BEGIN
              SELECT RAISE(ABORT, 'Evidence freshness records are append-only.');
            END;
            """)
            op.execute(f"""
            CREATE TRIGGER IF NOT EXISTS trg_{table}_no_delete
            BEFORE DELETE ON {table}
            BEGIN
              SELECT RAISE(ABORT, 'Evidence freshness records are append-only.');
            END;
            """)
    else:
        raise RuntimeError(f"Evidence freshness immutability is unsupported for {bind.dialect.name!r}")


def downgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        for table in ("evidence_freshness_assessments", "evidence_freshness_policies"):
            op.execute(f"DROP TRIGGER IF EXISTS trg_{table}_immutable ON {table};")
        op.execute("DROP FUNCTION IF EXISTS prevent_evidence_freshness_mutation();")
    op.drop_table("evidence_freshness_assessments")
    op.drop_table("evidence_freshness_policies")
