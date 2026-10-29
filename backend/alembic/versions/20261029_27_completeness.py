"""Add immutable purpose-specific completeness policies and assessments.

Revision ID: 20261029_27_completeness
Revises: 20261029_26_companion_refs
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "20261029_27_completeness"
down_revision = "20261029_26_companion_refs"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    if not inspector.has_table("evidence_completeness_policies"):
        op.create_table(
            "evidence_completeness_policies",
            sa.Column("id", sa.String(), primary_key=True),
            sa.Column("person_id", sa.String(), sa.ForeignKey("persons.id", ondelete="CASCADE"), nullable=False),
            sa.Column("domain_key", sa.String(length=80), nullable=False),
            sa.Column("purpose_key", sa.String(length=120), nullable=False),
            sa.Column("policy_version", sa.String(length=64), nullable=False),
            sa.Column("required_fields", sa.Text(), nullable=False),
            sa.Column("rationale", sa.Text(), nullable=False),
            sa.Column("created_by_caregiver_id", sa.String(), sa.ForeignKey("caregivers.id"), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.UniqueConstraint(
                "person_id", "domain_key", "purpose_key", "policy_version",
                name="uq_evidence_completeness_policy_version",
            ),
        )
    if not inspector.has_table("evidence_completeness_assessments"):
        op.create_table(
            "evidence_completeness_assessments",
            sa.Column("id", sa.String(), primary_key=True),
            sa.Column("person_id", sa.String(), sa.ForeignKey("persons.id", ondelete="CASCADE"), nullable=False),
            sa.Column("policy_id", sa.String(), sa.ForeignKey("evidence_completeness_policies.id", ondelete="RESTRICT"), nullable=False),
            sa.Column("policy_version", sa.String(length=64), nullable=False),
            sa.Column("subject_type", sa.String(length=80), nullable=False),
            sa.Column("subject_id", sa.String(length=160), nullable=False),
            sa.Column("assessment_status", sa.String(length=24), nullable=False),
            sa.Column("required_fields", sa.Text(), nullable=False),
            sa.Column("satisfied_fields", sa.Text(), nullable=False),
            sa.Column("missing_fields", sa.Text(), nullable=False),
            sa.Column("field_evidence_json", sa.Text(), nullable=False),
            sa.Column("evidence_ids", sa.Text(), nullable=False),
            sa.Column("rationale", sa.Text(), nullable=False),
            sa.Column("assessment_basis", sa.String(length=48), nullable=False),
            sa.Column("semantic_sufficiency", sa.String(length=40), nullable=False),
            sa.Column("assessed_by_caregiver_id", sa.String(), sa.ForeignKey("caregivers.id"), nullable=False),
            sa.Column("assessed_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        )

    inspector = inspect(bind)
    expected = {
        "evidence_completeness_policies": {
            "id", "person_id", "domain_key", "purpose_key", "policy_version",
            "required_fields", "rationale", "created_by_caregiver_id", "created_at",
        },
        "evidence_completeness_assessments": {
            "id", "person_id", "policy_id", "policy_version", "subject_type", "subject_id",
            "assessment_status", "required_fields", "satisfied_fields", "missing_fields",
            "field_evidence_json", "evidence_ids", "rationale", "assessment_basis",
            "semantic_sufficiency", "assessed_by_caregiver_id", "assessed_at",
        },
    }
    for table, required_columns in expected.items():
        actual = {column["name"] for column in inspector.get_columns(table)}
        missing = required_columns - actual
        if missing:
            raise RuntimeError(f"{table} is missing required columns: {', '.join(sorted(missing))}")

    indexes = (
        ("ix_evidence_completeness_policies_person_domain", "evidence_completeness_policies", ["person_id", "domain_key"]),
        ("ix_evidence_completeness_assessments_person_policy", "evidence_completeness_assessments", ["person_id", "policy_id", "assessed_at"]),
        ("ix_evidence_completeness_assessments_person_subject", "evidence_completeness_assessments", ["person_id", "subject_type", "subject_id"]),
    )
    for name, table, columns in indexes:
        current = {index["name"] for index in inspect(bind).get_indexes(table)}
        if name not in current:
            op.create_index(name, table, columns, unique=False)

    if bind.dialect.name == "postgresql":
        op.execute("""
            CREATE OR REPLACE FUNCTION reject_evidence_completeness_mutation()
            RETURNS trigger AS $$
            BEGIN
                RAISE EXCEPTION 'Evidence completeness policies and assessments are append-only';
            END;
            $$ LANGUAGE plpgsql;
        """)
        for table in ("evidence_completeness_policies", "evidence_completeness_assessments"):
            trigger = "trg_" + table + "_immutable"
            op.execute(f"""
                DROP TRIGGER IF EXISTS {trigger} ON {table};
                CREATE TRIGGER {trigger}
                BEFORE UPDATE OR DELETE ON {table}
                FOR EACH ROW EXECUTE FUNCTION reject_evidence_completeness_mutation();
            """)


def downgrade() -> None:
    raise RuntimeError(
        "Refusing to delete evidence completeness policies or assessment history. "
        "Use a forward-only compatibility migration."
    )
