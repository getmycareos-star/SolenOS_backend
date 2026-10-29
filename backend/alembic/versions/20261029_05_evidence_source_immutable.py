"""Make original evidence source text and its fingerprint immutable.

Revision ID: 20261029_05_evidence_immut
Revises: 20261029_04_evidence_kind
"""
from alembic import op
from sqlalchemy import inspect

revision = "20261029_05_evidence_immut"
down_revision = "20261029_04_evidence_kind"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    columns = {column["name"] for column in inspect(bind).get_columns("evidence")}
    if not {"source_text", "source_text_sha256"}.issubset(columns):
        raise RuntimeError("Evidence source text and fingerprint columns are required before immutability can be enforced")

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
