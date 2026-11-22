"""Protect caregiver review-decision history from in-place mutation.

Revision ID: 20261122_01_qwen_job_event_update_guard
Revises: 20261121_04_notification_attempt_id_width

QwenJobEvent is the durable audit trail for review decisions and other model-job
lifecycle transitions. Updates would rewrite history. Deletes remain permitted
for authorized privacy erasure through the existing parent-row cascade.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "20261122_01_qwen_job_event_update_guard"
down_revision = "20261121_04_notification_attempt_id_width"
branch_labels = None
depends_on = None

TABLE = "qwen_job_events"
TRIGGER = "trg_qwen_job_events_no_update"
FUNCTION = "reject_qwen_job_event_update"


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return
    if not inspect(bind).has_table(TABLE):
        raise RuntimeError(f"Cannot protect review history: {TABLE} is missing")

    op.execute(sa.text(f"""
        CREATE OR REPLACE FUNCTION {FUNCTION}()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            RAISE EXCEPTION 'qwen_job_events is append-only; in-place updates are not allowed';
        END;
        $$
    """))
    op.execute(sa.text(f"""
        DROP TRIGGER IF EXISTS {TRIGGER} ON {TABLE}
    """))
    op.execute(sa.text(f"""
        CREATE TRIGGER {TRIGGER}
        BEFORE UPDATE ON {TABLE}
        FOR EACH ROW
        EXECUTE FUNCTION {FUNCTION}()
    """))

    installed = bind.execute(sa.text("""
        SELECT COUNT(*)
        FROM pg_trigger
        WHERE tgrelid = 'qwen_job_events'::regclass
          AND tgname = :trigger_name
          AND NOT tgisinternal
    """), {"trigger_name": TRIGGER}).scalar_one()
    if installed != 1:
        raise RuntimeError("Qwen job event update guard was not installed")


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql" or not inspect(bind).has_table(TABLE):
        return
    op.execute(sa.text(f"DROP TRIGGER IF EXISTS {TRIGGER} ON {TABLE}"))
    op.execute(sa.text(f"DROP FUNCTION IF EXISTS {FUNCTION}()"))
