"""Enforce immutable evidence review history in PostgreSQL.

Revision ID: 20261028_06_review_immutable
Revises: 20261028_05_conflict_scope
"""
from alembic import op
from sqlalchemy import inspect, text

revision = "20261028_06_review_immutable"
down_revision = "20261028_05_conflict_scope"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    if not inspect(bind).has_table("evidence_review_records"):
        raise RuntimeError("Evidence review history must exist before immutability is enabled")
    if bind.dialect.name == "postgresql":
        bind.execute(text("""
            CREATE OR REPLACE FUNCTION reject_evidence_review_mutation()
            RETURNS trigger AS $$
            BEGIN
                RAISE EXCEPTION 'Evidence review records are append-only; create a new review version';
            END;
            $$ LANGUAGE plpgsql
        """))
        bind.execute(text("""
            DROP TRIGGER IF EXISTS trg_evidence_review_records_immutable
            ON evidence_review_records
        """))
        bind.execute(text("""
            CREATE TRIGGER trg_evidence_review_records_immutable
            BEFORE UPDATE OR DELETE ON evidence_review_records
            FOR EACH ROW EXECUTE FUNCTION reject_evidence_review_mutation()
        """))


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        bind.execute(text("""
            DROP TRIGGER IF EXISTS trg_evidence_review_records_immutable
            ON evidence_review_records
        """))
        bind.execute(text("DROP FUNCTION IF EXISTS reject_evidence_review_mutation()"))
