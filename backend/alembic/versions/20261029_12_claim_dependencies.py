"""Add an auditable, cycle-safe claim dependency graph.

Revision ID: 20261029_12_claim_deps
Revises: 20261029_11_cov_prov
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "20261029_12_claim_deps"
down_revision = "20261029_11_cov_prov"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    if not inspector.has_table("care_assertions"):
        raise RuntimeError("care_assertions must exist before claim dependencies can be stored.")

    required = {
        "id", "person_id", "claim_id", "depends_on_claim_id", "dependency_type",
        "rationale", "created_by_caregiver_id", "created_at",
    }
    if not inspector.has_table("evidence_claim_dependencies"):
        op.create_table(
            "evidence_claim_dependencies",
            sa.Column("id", sa.String(), primary_key=True),
            sa.Column("person_id", sa.String(), sa.ForeignKey("persons.id", ondelete="CASCADE"), nullable=False),
            sa.Column("claim_id", sa.String(), sa.ForeignKey("care_assertions.id", ondelete="CASCADE"), nullable=False),
            sa.Column("depends_on_claim_id", sa.String(), sa.ForeignKey("care_assertions.id", ondelete="RESTRICT"), nullable=False),
            sa.Column("dependency_type", sa.String(length=32), nullable=False),
            sa.Column("rationale", sa.Text(), nullable=False),
            sa.Column("created_by_caregiver_id", sa.String(), sa.ForeignKey("caregivers.id"), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.UniqueConstraint(
                "person_id", "claim_id", "depends_on_claim_id", "dependency_type",
                name="uq_evidence_claim_dependency",
            ),
        )
        inspector = inspect(bind)
    else:
        actual = {column["name"] for column in inspector.get_columns("evidence_claim_dependencies")}
        missing = required - actual
        if missing:
            raise RuntimeError(
                "Existing evidence_claim_dependencies schema is incompatible; missing columns: "
                f"{sorted(missing)}"
            )
        unique_constraints = inspector.get_unique_constraints("evidence_claim_dependencies")
        if not any(
            set(item.get("column_names") or []) ==
            {"person_id", "claim_id", "depends_on_claim_id", "dependency_type"}
            for item in unique_constraints
        ):
            raise RuntimeError("Existing claim dependency table lacks relationship uniqueness enforcement.")

    indexes = {item["name"] for item in inspect(bind).get_indexes("evidence_claim_dependencies")}
    for name, columns in (
        ("ix_evidence_claim_dependencies_person_claim", ["person_id", "claim_id"]),
        ("ix_evidence_claim_dependencies_person_depends", ["person_id", "depends_on_claim_id"]),
    ):
        if name not in indexes:
            op.create_index(name, "evidence_claim_dependencies", columns)

    if bind.dialect.name == "postgresql":
        op.execute("""
            CREATE OR REPLACE FUNCTION solenos_prevent_claim_dependency_mutation()
            RETURNS trigger LANGUAGE plpgsql
            AS 'BEGIN
              RAISE EXCEPTION ''Claim dependency edges are append-only; create a new relationship instead.'';
            END;'
        """)
        op.execute("""
            DO $trigger$
            BEGIN
              IF NOT EXISTS (
                SELECT 1 FROM pg_trigger
                WHERE tgname = 'trg_evidence_claim_dependencies_immutable'
                  AND NOT tgisinternal
              ) THEN
                CREATE TRIGGER trg_evidence_claim_dependencies_immutable
                BEFORE UPDATE OR DELETE ON evidence_claim_dependencies
                FOR EACH ROW EXECUTE FUNCTION solenos_prevent_claim_dependency_mutation();
              END IF;
            END;
            $trigger$;
        """)
    elif bind.dialect.name == "sqlite":
        op.execute("""
            CREATE TRIGGER IF NOT EXISTS trg_evidence_claim_dependencies_no_update
            BEFORE UPDATE ON evidence_claim_dependencies
            BEGIN
              SELECT RAISE(ABORT, 'Claim dependency edges are append-only.');
            END
        """)
        op.execute("""
            CREATE TRIGGER IF NOT EXISTS trg_evidence_claim_dependencies_no_delete
            BEFORE DELETE ON evidence_claim_dependencies
            BEGIN
              SELECT RAISE(ABORT, 'Claim dependency edges are append-only.');
            END
        """)
    else:
        raise RuntimeError(
            f"Claim dependency immutability is unsupported for database dialect {bind.dialect.name!r}"
        )


def downgrade() -> None:
    raise RuntimeError("Refusing to drop append-only claim dependency history.")
