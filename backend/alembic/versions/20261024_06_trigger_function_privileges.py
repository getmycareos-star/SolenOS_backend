"""Restrict public execution and pin search paths on trigger functions.

Revision ID: 20261024_06
Revises: 20261024_05

The functions are invoked by PostgreSQL triggers/event triggers, not as public
RPC endpoints. Revoke direct execution from PUBLIC/anon/authenticated while
preserving trigger execution. Pin name resolution to pg_catalog.
"""
from alembic import op

revision = "20261024_06"
down_revision = "20261024_05"
branch_labels = None
depends_on = None


def upgrade():
    # The function privileges and regprocedure syntax are PostgreSQL-specific.
    # SQLite is used only for migration smoke tests; PostgreSQL CI exercises this DDL.
    if op.get_bind().dialect.name != "postgresql":
        return
    op.execute(
        """
        DO $migration$
        DECLARE
            target regprocedure;
            role_name text;
        BEGIN
            FOREACH target IN ARRAY ARRAY[
                to_regprocedure('public.rls_auto_enable()'),
                to_regprocedure('public.set_updated_at()')
            ]
            LOOP
                IF target IS NULL THEN
                    CONTINUE;
                END IF;

                EXECUTE format(
                    'ALTER FUNCTION %s SET search_path = pg_catalog',
                    target
                );
                EXECUTE format(
                    'REVOKE EXECUTE ON FUNCTION %s FROM PUBLIC',
                    target
                );

                FOREACH role_name IN ARRAY ARRAY['anon', 'authenticated']
                LOOP
                    IF EXISTS (
                        SELECT 1 FROM pg_roles WHERE rolname = role_name
                    ) THEN
                        EXECUTE format(
                            'REVOKE EXECUTE ON FUNCTION %s FROM %I',
                            target,
                            role_name
                        );
                    END IF;
                END LOOP;
            END LOOP;
        END
        $migration$;
        """
    )


def downgrade():
    raise RuntimeError(
        "Forward-only security migration; restoring public function execution "
        "requires a separately reviewed security decision."
    )
