"""Reconcile caregiver timezone provenance against later notification updates.

Revision ID: 20261124_03_timezone_notification_provenance
Revises: 20261124_02_caregiver_timezone_authority
"""
from alembic import op
from sqlalchemy import inspect, text

revision = "20261124_03_timezone_notification_provenance"
down_revision = "20261124_02_caregiver_timezone_authority"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    required_tables = {"caregivers", "audit_log_events", "care_notification_preferences"}
    missing_tables = {table for table in required_tables if not inspector.has_table(table)}
    if missing_tables:
        raise RuntimeError(
            "Cannot reconcile caregiver timezone provenance; missing tables: "
            + ", ".join(sorted(missing_tables))
        )

    notification_columns = {
        column["name"] for column in inspector.get_columns("care_notification_preferences")
    }
    if not {"person_id", "caregiver_id", "updated_at"}.issubset(notification_columns):
        raise RuntimeError("Notification timezone provenance columns are missing.")
    if bind.dialect.name != "postgresql":
        raise RuntimeError("Caregiver timezone provenance reconciliation requires PostgreSQL JSONB semantics.")

    # A caregiver-preference audit proves an explicit choice only until a later
    # notification-preference update may have overwritten the legacy shared
    # timezone field. Notification updated_at is row-level, so this deliberately
    # prefers a safe unknown timezone over preserving an ambiguous value.
    bind.execute(text("""
        UPDATE caregivers AS caregiver
        SET timezone = 'UTC',
            timezone_configured = FALSE
        WHERE caregiver.timezone_configured = TRUE
          AND EXISTS (
              SELECT 1
              FROM care_notification_preferences AS notification
              WHERE notification.person_id = caregiver.person_id
                AND notification.caregiver_id = caregiver.id
                AND notification.updated_at IS NOT NULL
                AND NOT EXISTS (
                    SELECT 1
                    FROM audit_log_events AS audit
                    WHERE audit.caregiver_id = caregiver.id
                      AND audit.person_id = caregiver.person_id
                      AND audit.action = 'caregiver.preferences.update'
                      AND audit.target_type = 'caregiver_preferences'
                      AND audit.target_id = caregiver.id
                      AND COALESCE(
                            ((audit.metadata::jsonb -> 'updated_fields') ? 'timezone'),
                            FALSE
                      )
                      AND audit.occurred_at >= notification.updated_at
                )
          )
    """))

    if inspector.has_table("caregiver_profiles"):
        profile_columns = {
            column["name"] for column in inspector.get_columns("caregiver_profiles")
        }
        if {"person_id", "session_caregiver_id", "timezone"}.issubset(profile_columns):
            bind.execute(text("""
                UPDATE caregiver_profiles AS profile
                SET timezone = 'UTC'
                FROM caregivers AS caregiver
                WHERE profile.person_id = caregiver.person_id
                  AND profile.session_caregiver_id = caregiver.id
                  AND caregiver.timezone_configured = FALSE
            """))


def downgrade() -> None:
    # This is a provenance correction; the previous shared-field value cannot
    # be restored safely because notification updates did not record its source.
    pass
