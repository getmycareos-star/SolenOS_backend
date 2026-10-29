"""Make original evidence source text and its fingerprint immutable.

Revision ID: 20261029_05_evidence_immut
Revises: 20261029_04_evidence_kind
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "20261029_05_evidence_immut"
down_revision = "20261029_04_evidence_kind"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    if not inspector.has_table("evidence"):
        raise RuntimeError("The evidence table must exist before source immutability can be enforced")
    columns = {column["name"] for column in inspector.get_columns("evidence")}

    # Some older account-oriented databases have an evidence table without the
    # canonical text field. Add nullable fields without guessing which legacy
    # column might represent source text. Existing unknown values stay unknown.
    if "source_text" not in columns:
        op.add_column("evidence", sa.Column("source_text", sa.Text(), nullable=True))
        columns.add("source_text")
    if "source_text_sha256" not in columns:
        op.add_column("evidence", sa.Column("source_text_sha256", sa.String(length=64), nullable=True))
        columns.add("source_text_sha256")

    if bind.dialect.name == "postgresql":
        op.execute("""
            CREATE OR REPLACE FUNCTION solenos_prevent_evidence_source_mutation()
            RETURNS trigger
            LANGUAGE plpgsql
            AS 'BEGIN
              IF NEW.source_text IS DISTINCT FROM OLD.source_text
                 OR NEW.source_text_sha256 IS DISTINCT FROM OLD.source_text_sha256 THEN
                RAISE EXCEPTION ''Original evidence source text and fingerprint are immutable; record a correction or derived evidence instead.'';
              END IF;
              RETURN NEW;
            END;'
        """)
        op.execute("""
            DO $trigger$
            BEGIN
              IF NOT EXISTS (
                SELECT 1 FROM pg_trigger
                WHERE tgname = 'trg_evidence_source_immutable'
                  AND NOT tgisinternal
              ) THEN
                CREATE TRIGGER trg_evidence_source_immutable
                BEFORE UPDATE OF source_text, source_text_sha256 ON evidence
                FOR EACH ROW EXECUTE FUNCTION solenos_prevent_evidence_source_mutation();
              END IF;
            END;
            $trigger$;
        """)
    elif bind.dialect.name == "sqlite":
        op.execute("""
            CREATE TRIGGER IF NOT EXISTS trg_evidence_source_immutable
            BEFORE UPDATE OF source_text, source_text_sha256 ON evidence
            WHEN NEW.source_text IS NOT OLD.source_text
              OR NEW.source_text_sha256 IS NOT OLD.source_text_sha256
            BEGIN
              SELECT RAISE(ABORT, 'Original evidence source text and fingerprint are immutable; record a correction or derived evidence instead.');
            END;
        """)
    else:
        raise RuntimeError(
            f"Evidence source immutability is not implemented for database dialect {bind.dialect.name!r}"
        )


def downgrade() -> None:
    raise RuntimeError(
        "Refusing to remove original evidence source immutability; preserve the audit boundary."
    )
