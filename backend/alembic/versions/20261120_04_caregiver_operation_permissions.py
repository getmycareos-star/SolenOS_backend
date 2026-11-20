"""Add persisted, per-member caregiver operation permissions.

Revision ID: 20261120_04_caregiver_operation_permissions
Revises: 20261120_03_care_event_ingestion_keys
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "20261120_04_caregiver_operation_permissions"
down_revision = "20261120_03_care_event_ingestion_keys"
branch_labels = None
depends_on = None

DEFAULT_FAMILY_PERMISSIONS = [
    "view_lcr", "add_evidence", "manage_responsibilities", "manage_reminders",
    "complete_assigned_reminders", "manage_handoffs", "share_summaries",
    "share_observations", "view_documents", "view_communications",
]


def upgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    if not inspector.has_table("caregiver_access_grants") or not inspector.has_table("caregiver_invitations"):
        raise RuntimeError("Caregiver membership tables must exist before adding operation permissions")
    grant_columns = {c["name"] for c in inspector.get_columns("caregiver_access_grants")}
    invitation_columns = {c["name"] for c in inspector.get_columns("caregiver_invitations")}
    if "permissions" not in grant_columns:
        op.add_column("caregiver_access_grants", sa.Column("permissions", sa.JSON(), nullable=True))
    if "permissions" not in invitation_columns:
        op.add_column("caregiver_invitations", sa.Column("permissions", sa.JSON(), nullable=True))

    grants = sa.table(
        "caregiver_access_grants",
        sa.column("id", sa.String()), sa.column("access_scope", sa.String()),
        sa.column("permissions", sa.JSON()),
    )
    for row in bind.execute(sa.select(grants.c.id, grants.c.access_scope, grants.c.permissions)).mappings():
        if row["permissions"] is None:
            value = [] if row["access_scope"] == "documents_only" else DEFAULT_FAMILY_PERMISSIONS
            bind.execute(grants.update().where(grants.c.id == row["id"]).values(permissions=value))

    invitations = sa.table(
        "caregiver_invitations", sa.column("id", sa.String()), sa.column("permissions", sa.JSON())
    )
    bind.execute(invitations.update().where(invitations.c.permissions.is_(None)).values(permissions=DEFAULT_FAMILY_PERMISSIONS))
    op.alter_column("caregiver_access_grants", "permissions", existing_type=sa.JSON(), nullable=False, server_default=sa.text("'[]'"))
    op.alter_column("caregiver_invitations", "permissions", existing_type=sa.JSON(), nullable=False, server_default=sa.text("'[]'"))


def downgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    if inspector.has_table("caregiver_invitations") and "permissions" in {c["name"] for c in inspector.get_columns("caregiver_invitations")}:
        op.drop_column("caregiver_invitations", "permissions")
    if inspector.has_table("caregiver_access_grants") and "permissions" in {c["name"] for c in inspect(bind).get_columns("caregiver_access_grants")}:
        op.drop_column("caregiver_access_grants", "permissions")
