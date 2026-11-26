"""Add expiring cross-worker Companion turn leases.

Revision ID: 20261126_01_companion_turn_leases
Revises: 20261124_03_timezone_notification_provenance
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect, text

revision = "20261126_01_companion_turn_leases"
down_revision = "20261124_03_timezone_notification_provenance"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        raise RuntimeError("Companion turn leases require PostgreSQL.")
    inspector = inspect(bind)
    if not inspector.has_table("companion_turn_leases"):
        op.create_table(
            "companion_turn_leases",
            sa.Column("lock_key", sa.String(length=160), primary_key=True),
            op.Column("owner_token", op.String(length=36), nullable=False),
            op.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
            op.Column(
                "updated_at",
                op.DateTime(timezone=True),
                nullable=False,
                server_default=text("now()"),
            ),
        )
    inspector = inspect(bind)
    columns = {column["name"] for column in inspector.get_columns("companion_turn_leases")}
    required = {"lock_key", "owner_token", "expires_at", "updated_at"}
    if not required.issubset(columns):
        raise RuntimeError(
            "Companion turn lease schema is incomplete: "
            + ", ".join(sorted(required - columns))
        )
    indexes = {index["name"] for index in inspector.get_indexes("companion_turn_leases")}
    if "ix_companion_turn_leases_owner_token" not in indexes:
        op.create_index(
            "ix_companion_turn_leases_owner_token",
            "companion_turn_leases",
            ["owner_token"],
        )
    if "ix_companion_turn_leases_expires_at" not in indexes:
        op.create_index(
            "ix_companion_turn_leases_expires_at",
            "companion_turn_leases",
            ["expires_at"],
        )


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return
    inspector = inspect(bind)
    if not inspector.has_table("companion_turn_leases"):
        return
    indexes = {index["name"] for index in inspector.get_indexes("companion_turn_leases")}
    for name in (
        "ix_companion_turn_leases_expires_at",
        "ix_companion_turn_leases_owner_token",
    ):
        if name in indexes:
            op.drop_index(name, table_name="companion_turn_leases")
    op.drop_table("companion_turn_leases")
