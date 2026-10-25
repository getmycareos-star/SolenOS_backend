"""Add email/password accounts scoped to a single care record.

Revision ID: 20261025_02_accounts
Revises: 20261025_01
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "20261025_02_accounts"
down_revision = "20261025_01"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    if not inspect(bind).has_table("caregiver_accounts"):
        op.create_table(
            "caregiver_accounts",
            sa.Column("id", sa.String(), primary_key=True),
            sa.Column("email_normalized", sa.String(length=320), nullable=False),
            sa.Column("password_hash", sa.String(length=256), nullable=False),
            sa.Column("person_id", sa.String(), sa.ForeignKey("persons.id", ondelete="CASCADE"), nullable=False),
            sa.Column("caregiver_id", sa.String(), sa.ForeignKey("caregivers.id", ondelete="CASCADE"), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.UniqueConstraint("email_normalized", name="uq_caregiver_accounts_email_normalized"),
            sa.UniqueConstraint("person_id", name="uq_caregiver_accounts_person_id"),
        )
    inspector = inspect(bind)
    if inspector.has_table("anonymous_sessions"):
        columns = {column["name"] for column in inspector.get_columns("anonymous_sessions")}
        if "account_id" not in columns:
            op.add_column("anonymous_sessions", sa.Column("account_id", sa.String(), nullable=True))
        if bind.dialect.name != "sqlite":
            foreign_keys = {item.get("name") for item in inspect(bind).get_foreign_keys("anonymous_sessions")}
            if "fk_anonymous_sessions_account_id_caregiver_accounts" not in foreign_keys:
                op.create_foreign_key(
                    "fk_anonymous_sessions_account_id_caregiver_accounts",
                    "anonymous_sessions", "caregiver_accounts",
                    ["account_id"], ["id"], ondelete="SET NULL",
                )
        indexes = {item["name"] for item in inspect(bind).get_indexes("anonymous_sessions")}
        if "ix_anonymous_sessions_account_id" not in indexes:
            op.create_index("ix_anonymous_sessions_account_id", "anonymous_sessions", ["account_id"])
    inspector = inspect(bind)
    if inspector.has_table("caregiver_accounts"):
        indexes = {item["name"] for item in inspector.get_indexes("caregiver_accounts")}
        for name, columns in [
            ("ix_caregiver_accounts_person_id", ["person_id"]),
            ("ix_caregiver_accounts_caregiver_id", ["caregiver_id"]),
        ]:
            if name not in indexes:
                op.create_index(name, "caregiver_accounts", columns)


def downgrade():
    bind = op.get_bind()
    inspector = inspect(bind)
    if inspector.has_table("anonymous_sessions"):
        indexes = {item["name"] for item in inspector.get_indexes("anonymous_sessions")}
        if "ix_anonymous_sessions_account_id" in indexes:
            op.drop_index("ix_anonymous_sessions_account_id", table_name="anonymous_sessions")
        if bind.dialect.name != "sqlite":
            foreign_keys = {item.get("name") for item in inspect(bind).get_foreign_keys("anonymous_sessions")}
            if "fk_anonymous_sessions_account_id_caregiver_accounts" in foreign_keys:
                op.drop_constraint("fk_anonymous_sessions_account_id_caregiver_accounts", "anonymous_sessions", type_="foreignkey")
        columns = {column["name"] for column in inspect(bind).get_columns("anonymous_sessions")}
        if "account_id" in columns:
            op.drop_column("anonymous_sessions", "account_id")
    if inspect(bind).has_table("caregiver_accounts"):
        op.drop_table("caregiver_accounts")
