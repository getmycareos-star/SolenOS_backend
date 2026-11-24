"""Remove notification-only timezone values from Companion authority.

Revision ID: 20261124_02_caregiver_timezone_authority
Revises: 20261124_01_caregiver_preferences
"""
from alembic import op
from sqlalchemy import inspect, text

revision = "20261124_02_caregiver_timezone_authority"
down_revision = "20261124_01_caregiver_preferences"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    if not inspector.has_table("caregivers") or not inspector.has_table("audit_log_events"):
        raise RuntimeError(
            "Cannot establish caregiver timezone authority without caregivers and audit_log_events."
        )
    caregiver_columns = {column["name"] for column in inspector.get_columns("caregivers")}
    audit_columns = {column["name"] for column in inspector.get_columns("audit_log_events")}
    if not {"timezone", "timezone_configured"}.issubset(caregiver_columns):
        raise RuntimeError("Caregiver timezone authority columns are missing.")
    if not {"caregiver_id", "person_id", "action", "target_type", "target_id", "metadata"}.issubset(audit_columns):
        raise RuntimeError("Audit provenance columns are missing; refusing to infer caregiver timezone authority.")
    if bind.dialect.name != "postgresql":
        raise RuntimeError("Caregiver timezone provenance migration requires PostgreSQL JSONB semantics.")

    # Older notification settings also wrote their timezone into caregivers.
    # Those rows cannot prove that the caregiver selected a Companion timezone.
    # Preserve only an explicit caregiver-preferences update recorded by the
    # current authenticated preference endpoint; reset every unproven value.
    bind.execute(text("""
        UPDATE caregivers AS caregiver
        SET timezone = 'UTC',
            timezone_configured = FALSE
        WHERE NOT EXISTS (
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
        )
    """))

    # Keep the legacy profile mirror aligned with the authoritative caregiver
    # preference. It is not an independent source of timezone authority.
    if inspector.has_table("caregiver_profiles"):
        profile_columns = {column["name"] for column in inspector.get_columns("caregiver_profiles")}
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
    # This is an intentional provenance correction. The old values cannot be
    # restored safely because their source was not recorded.
    pass
