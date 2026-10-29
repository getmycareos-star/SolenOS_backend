"""Protect observation coverage history from mutation or deletion.
Revision ID: 20261029_10_coverage_immutable
Revises: 20261029_09_obs
"""
from alembic import op
from sqlalchemy import inspect

revision = "20261029_10_coverage_immutable"
down_revision = "20261029_09_obs"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    if not inspect(bind).has_table("observation_coverage"):
        raise RuntimeError("Observation coverage must exist before its audit history can be protected.")

    if bind.dialect.name == "postgresql":
        op.execute("""
            CREATE OR REPLACE FUNCTION solenos_prevent_observation_coverage_mutation()
            RETURNS trigger
            LANGUAGE plpgsql
            AS 'BEGIN
              RAISE EXCEPTION ''Observation coverage is append-only; record a correction as a new coverage record.'';
            END;'
        """)
        op.execute("""
            DO $trigger$
            BEGIN
              IF NOT EXISTS (
                SELECT 1 FROM pg_trigger
                WHERE tgname = 'trg_observation_coverage_immutable'
                  AND NOT tgisinternal
              ) THEN
                CREATE TRIGGER trg_observation_coverage_immutable
                BEFORE UPDATE OR DELETE ON observation_coverage
                FOR EACH ROW EXECUTE FUNCTION solenos_prevent_observation_coverage_mutation();
              END IF;
            END;
            $trigger$;
        """)
    elif bind.dialect.name == "sqlite":
        op.execute("""
            CREATE TRIGGER IF NOT EXISTS trg_observation_coverage_no_update
            BEFORE UPDATE ON observation_coverage
            BEGIN
              SELECT RAISE(ABORT, 'Observation coverage is append-only; record a correction as a new coverage record.');
            END;
        """)
        op.execute("""
            CREATE TRIGGER IF NOT EXISTS trg_observation_coverage_no_delete
            BEFORE DELETE ON observation_coverage
            BEGIN
              SELECT RAISE(ABORT, 'Observation coverage is append-only; preserve the audit history.');
            END;
        """)
    else:
        raise RuntimeError(
            f"Observation coverage immutability is not implemented for database dialect {bind.dialect.name!r}"
        )


def downgrade() -> None:
    raise RuntimeError("Refusing to remove observation-coverage audit protections.")
