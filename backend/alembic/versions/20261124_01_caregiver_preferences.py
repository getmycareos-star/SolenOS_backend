"""Persist caregiver display and locale preferences.

Revision ID: 20261124_01_caregiver_preferences
Revises: 20261123_02_care_notification_active_source_index
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect, text

revision = "20261124_01_caregiver_preferences"
down_revision = "20261123_02_care_notification_active_source_index"
branch_labels = None
depends_on = None

COLUMNS = {
    "timezone_configured": sa.Column("timezone_configured", sa.Boolean(), nullable=False, server_default=sa.false()),
    "preferred_name": sa.Column("preferred_name", sa.String(80), nullable=True),
    "locale": sa.Column("locale", sa.String(16), nullable=True),
    "date_format": sa.Column("date_format", sa.String(24), nullable=True),
    "measurement_system": sa.Column("measurement_system", sa.String(24), nullable=True),
    "communication_style": sa.Column("communication_style", sa.String(24), nullable=True),
}


def upgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    if not inspector.has_table("caregivers"):
        raise RuntimeError("Cannot add caregiver preferences: caregivers table is missing")
    existing = {column["name"] for column in inspector.get_columns("caregivers")}
    for name, column in COLUMNS.items():
        if name not in existing:
            op.add_column("caregivers", column)
            existing.add(name)
    # A non-UTC legacy timezone was deliberately supplied in prior flows.
    # A legacy UTC default is not proof that the caregiver confirmed UTC.
    bind.execute(text(
        "UPDATE caregivers SET timezone_configured = TRUE "
        "WHERE timezone IS NOT NULL AND TRIM(timezone) <> '' AND timezone <> 'UTC'"
    ))
    final = {column["name"] for column in inspect(bind).get_columns("caregivers")}
    missing = set(COLUMNS) - final
    if missing:
        raise RuntimeError("Caregiver preference migration incomplete: " + ", ".join(sorted(missing)))


def downgrade() -> None:
    bind = op.get_bind()
    existing = {column["name"] for column in inspect(bind).get_columns("caregivers")} if inspect(bind).has_table("caregivers") else set()
    for name in reversed(tuple(COLUMNS)):
        if name in existing:
            op.drop_column("caregivers", name)
