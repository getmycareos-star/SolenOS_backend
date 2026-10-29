"""Persist explicit domain-specific freshness policies and assessments.

Revision ID: 20261029_25_evidence_freshness_policy
Revises: 20261029_24_baseline_history
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "20261029_25_evidence_freshness_policy"
down_revision = "20261029_24_baseline_history"
branch_labels = None
depends_on = None


def _ensure_table(table_name: str, columns: list[str], create_table) -> None:
    """Resume safely if a prior interrupted upgrade committed table creation."""
    bind = op.get_bind()
    inspector = inspect(bind)
    if not inspector.has_table(table_name):
        create_table()
        inspector = inspect(bind)
    existing_columns = {column["name"] for column in inspector.get_columns(table_name)}
    missing_columns = set(columns) - existing_columns
    if missing_columns:
        raise RuntimeError(
            f"Existing {table_name} table is incomplete; missing columns: {sorted(missing_columns)}"
        )


def _ensure_index(table_name: str, index_name: str, columns: list[str]) -> None:
    if not inspect(op.get_bind()).has_index(table_name, index_name):
        op.create_index(index_name, table_name, columns)


def upgrade() -> None:
    _ensure_table(
        "evidence_freshness_policies",
        [
            "id", "person_id", "domain_key", "policy_version", "max_age_days",
            "historical_after_days", "rationale", "created_by_caregiver_id", "created_at",
        ],
        lambda: op.create_table(
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
        ),
    )
    _ensure_index("evidence_freshness_policies", "ix_evidence_freshness_policies_person_id", ["person_id"])
    _ensure_index("evidence_freshness_policies", "ix_evidence_freshness_policies_person_domain", ["person_id", "domain_key"])

    _ensure_table(
        "evidence_freshness_assessments",
        [
            "id", "person_id", "evidence_id", "policy_id", "policy_version", "freshness_status",
            "source_time", "assessed_at", "age_days", "rationale", "assessed_by_caregiver_id",
        ],
        lambda: op.create_table(
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
        ),
    )
    _ensure_index("evidence_freshness_assessments", "ix_evidence_freshness_assessments_person_id", ["person_id"])
    _ensure_index(
        "evidence_freshness_assessments",
        "ix_evidence_freshness_assessments_person_evidence",
        ["person_id", "evidence_id", "assessed_at"],
    )
    _ensure_index("evidence_freshness_assessments", "ix_evidence_freshness_assessments_policy", ["policy_id"])

    if op.get_bind().dialect.name == "postgresql":
        op.execute("""
        CREATE OR REPLACE FUNCTION prevent_evidence_freshness_mutation() RETURNS trigger AS $$
        BEGIN
          RAISE EXCEPTION 'evidence freshness policies and assessments are append-only';
        END;
        $$ LANGUAGE plpgsql;
        """)
        for table in ("evidence_freshness_policies", "evidence_freshness_assessments"):
            trigger_name = f"trg_{table}_immutable"
            trigger_exists = op.get_bind().exec_driver_sql(
                "SELECT 1 FROM pg_trigger WHERE tgname = %s AND NOT tgisinternal",
                (trigger_name,),
            ).first()
            if not trigger_exists:
                op.execute(
                    f"CREATE TRIGGER {trigger_name} BEFORE UPDATE OR DELETE ON {table} "
                    "FOR EACH ROW EXECUTE FUNCTION prevent_evidence_freshness_mutation();"
                )


def downgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        for table in ("evidence_freshness_assessments", "evidence_freshness_policies"):
            op.execute(f"DROP TRIGGER IF EXISTS trg_{table}_immutable ON {table};")
        op.execute("DROP FUNCTION IF EXISTS prevent_evidence_freshness_mutation();")
    op.drop_table("evidence_freshness_assessments")
    op.drop_table("evidence_freshness_policies")
