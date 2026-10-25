"""Add durable caregiver account security state.

Revision ID: 20261025_05
Revises: 20261025_04
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "20261025_05"
down_revision = "20261025_04"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    inspector = inspect(bind)
    if not inspector.has_table("caregiver_accounts"):
        raise RuntimeError("caregiver_accounts must exist before account security migration")
    columns = {column["name"] for column in inspector.get_columns("caregiver_accounts")}
    if "email_verified_at" not in columns:
        op.add_column("caregiver_accounts", sa.Column("email_verified_at", sa.DateTime(timezone=True), nullable=True))
    if "mfa_enabled" not in columns:
        op.add_column("caregiver_accounts", sa.Column("mfa_enabled", sa.Boolean(), nullable=False, server_default=sa.false()))
    if not inspector.has_table("account_security_tokens"):
        op.create_table(
            "account_security_tokens",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("account_id", sa.String(), sa.ForeignKey("caregiver_accounts.id", ondelete="CASCADE"), nullable=False),
            sa.Column("purpose", sa.String(32), nullable=False),
            sa.Column("token_hash", sa.String(64), nullable=False),
            sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column("request_ip_hash", sa.String(64), nullable=True),
            sa.UniqueConstraint("token_hash", name="uq_account_security_tokens_hash"),
        )
    if not inspector.has_table("account_mfa_secrets"):
        op.create_table(
            "account_mfa_secrets",
            sa.Column("account_id", sa.String(), sa.ForeignKey("caregiver_accounts.id", ondelete="CASCADE"), primary_key=True),
            sa.Column("secret_ciphertext", sa.Text(), nullable=False),
            sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        )
    if not inspector.has_table("account_mfa_recovery_codes"):
        op.create_table(
            "account_mfa_recovery_codes",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("account_id", sa.String(), sa.ForeignKey("caregiver_accounts.id", ondelete="CASCADE"), nullable=False),
            sa.Column("code_hash", sa.String(64), nullable=False),
            sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.UniqueConstraint("account_id", "code_hash", name="uq_account_mfa_recovery_code"),
        )
    if not inspector.has_table("account_login_attempts"):
        op.create_table(
            "account_login_attempts",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("email_hash", sa.String(64), nullable=False),
            sa.Column("ip_hash", sa.String(64), nullable=False),
            sa.Column("attempted_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column("succeeded", sa.Boolean(), nullable=False, server_default=sa.false()),
        )
    inspector = inspect(bind)
    for table, name, columns in [
        ("account_security_tokens", "ix_account_security_tokens_account_purpose", ["account_id", "purpose"]),
        ("account_security_tokens", "ix_account_security_tokens_expiry", ["expires_at"]),
        ("account_mfa_recovery_codes", "ix_account_mfa_recovery_codes_account", ["account_id"]),
        ("account_login_attempts", "ix_account_login_attempts_email_time", ["email_hash", "attempted_at"]),
        ("account_login_attempts", "ix_account_login_attempts_ip_time", ["ip_hash", "attempted_at"]),
    ]:
        indexes = {item["name"] for item in inspect(bind).get_indexes(table)}
        if name not in indexes:
            op.create_index(name, table, columns)


def downgrade():
    bind = op.get_bind()
    inspector = inspect(bind)
    for table in ("account_login_attempts", "account_mfa_recovery_codes", "account_mfa_secrets", "account_security_tokens"):
        if inspector.has_table(table):
            op.drop_table(table)
    columns = {column["name"] for column in inspect(bind).get_columns("caregiver_accounts")}
    with op.batch_alter_table("caregiver_accounts") as batch:
        if "mfa_enabled" in columns:
            batch.drop_column("mfa_enabled")
        if "email_verified_at" in columns:
            batch.drop_column("email_verified_at")
