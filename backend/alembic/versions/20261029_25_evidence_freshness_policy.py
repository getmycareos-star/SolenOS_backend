"""Persist explicit domain-specific freshness policies and assessments.

Revision ID: 20261029_25_freshness
Revises: 20261029_24_baseline_history
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "20261029_25_freshness"
down_revision = "20261029_24_baseline_history"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)

    # Local/test imports may bootstrap ORM tables before Alembic starts.
    # Production PostgreSQL normally reaches this migration without these
    # tables. Reconcile both paths rather than failing on DuplicateTable.
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

    inspector = inspect(bind)
    policy_columns = {column["name"] for column in inspector.get_columns("evidence_freshness_policies")}
    required_policy_columns = {
        "id", "person_id", "domain_key", "policy_version", "max_age_days",
        "historical_after_days", "rationale", "created_by_caregiver_id", "created_at",
    }
    if required_policy_columns - policy_columns:
        raise RuntimeError(
            "Existing evidence_freshness_policies table is incomplete: "
            + ", ".join(sorted(required_policy_columns - policy_columns))
        )

    policy_constraints = inspect(bind).get_unique_constraints("evidence_freshness_policies")
    policy_indexes = inspect(bind).get_indexes("evidence_freshness_policies")
    has_version_uniqueness = (
        any(item.get("name") == "uq_evidence_freshness_policy_version"
            and item.get("column_names") == ["person_id", "domain_key", "policy_version"]
            for item in policy_constraints)
        or any(item.get("unique")
            and item.get("column_names") == ["person_id", "domain_key", "policy_version"]
            for item in policy_indexes)
    )
    if not has_version_uniqueness:
        raise RuntimeError("Existing evidence_freshness_policies lacks unique person/domain/version enforcement")

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
            sa.Column("source_time_kind", sa.String(length=32), nullable=True),
            sa.Column("assessed_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("age_days", sa.Integer(), nullable=True),
            sa.Column("rationale", sa.Text(), nullable=False),
            sa.Column("assessed_by_caregiver_id", sa.String(), sa.ForeignKey("caregivers.id"), nullable=False),
        )

    inspector = inspect(bind)
    assessment_columns = {column["name"] for column in inspector.get_columns("evidence_freshness_assessments")}
    required_assessment_columns = {
        "id", "person_id", "evidence_id", "policy_id", "policy_version",
        "freshness_status", "source_time", "source_time_kind", "assessed_at", "age_days",
        "rationale", "assessed_by_caregiver_id",
    }
    if "source_time_kind" not in assessment_columns:
        op.add_column("evidence_freshness_assessments", sa.Column("source_time_kind", sa.String(length=32), nullable=True))
        assessment_columns.add("source_time_kind")
    if required_assessment_columns - assessment_columns:
        raise RuntimeError(
            "Existing evidence_freshness_assessments table is incomplete: "
            + ", ".join(sorted(required_assessment_columns - assessment_columns))
        )

    # create_all() and earlier partial deployments may have created tables and
    # some indexes already. Add missing indexes only; never drop/rebuild data.
    expected_indexes = (
        ("ix_evidence_freshness_policies_person_id", "evidence_freshness_policies", ["person_id"]),
        ("ix_evidence_freshness_policies_person_domain", "evidence_freshness_policies", ["person_id", "domain_key"]),
        ("ix_evidence_freshness_assessments_person_id", "evidence_freshness_assessments", ["person_id"]),
        ("ix_evidence_freshness_assessments_person_evidence", "evidence_freshness_assessments", ["person_id", "evidence_id", "assessed_at"]),
        ("ix_evidence_freshness_assessments_policy", "evidence_freshness_assessments", ["policy_id"]),
    )
    for name, table, columns in expected_indexes:
        existing = {item["name"] for item in inspect(bind).get_indexes(table)}
        if name not in existing:
            op.create_index(name, table, columns)

    if bind.dialect.name == "postgresql":
        op.execute("""
        CREATE OR REPLACE FUNCTION prevent_evidence_freshness_mutation() RETURNS trigger AS $$
        BEGIN
          RAISE EXCEPTION 'evidence freshness policies and assessments are append-only';
        END;
        $$ LANGUAGE plpgsql;
        """)
        for table in ("evidence_freshness_policies", "evidence_freshness_assessments"):
            trigger_name = f"trg_{table}_immutable"
            exists = bind.execute(
                sa.text("""
                    SELECT 1
                    FROM pg_trigger
                    WHERE tgname = :trigger_name
                      AND tgrelid = to_regclass(:table_name)
                      AND NOT tgisinternal
                """),
                {"trigger_name": trigger_name, "table_name": table},
            ).first()
            if not exists:
                op.execute(
                    f"CREATE TRIGGER {trigger_name} BEFORE UPDATE OR DELETE ON {table} "
                    "FOR EACH ROW EXECUTE FUNCTION prevent_evidence_freshness_mutation();"
                )
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
    raise RuntimeError(
        "Refusing to drop append-only freshness policy and assessment history. "
        "Deploy a forward-only compatibility change instead."
    )
