"""Add independent professional accounts and bearer sessions."""
from alembic import op
import sqlalchemy as sa
revision = "20261025_03"
down_revision = "20261025_02"
branch_labels = None
depends_on = None

def upgrade():
    op.create_table(
        "professional_accounts",
        sa.Column("id", sa.String(), primary_key=True, nullable=False),
        sa.Column("email", sa.String(length=254), nullable=False),
        sa.Column("password_hash", sa.String(length=255), nullable=False),
        sa.Column("full_name", sa.String(length=160), nullable=False),
        sa.Column("professional_title", sa.String(length=160), nullable=False),
        sa.Column("organization", sa.String(length=200), nullable=True),
        sa.Column("credentials", sa.String(length=300), nullable=True),
        sa.Column("specialties", sa.Text(), nullable=True),
        sa.Column("biography", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=24), nullable=False, server_default="pending_review"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("email", name="uq_professional_accounts_email"))
    op.create_index("ix_professional_accounts_email", "professional_accounts", ["email"], unique=True)
    op.create_index("ix_professional_accounts_status", "professional_accounts", ["status"], unique=False)
    op.create_table(
        "professional_sessions",
        sa.Column("id", sa.String(), primary_key=True, nullable=False),
        sa.Column("professional_id", sa.String(), sa.ForeignKey("professional_accounts.id", ondelete="CASCADE"), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("token_hash", name="uq_professional_sessions_token_hash"))
    op.create_index("ix_professional_sessions_professional_id", "professional_sessions", ["professional_id"], unique=False)
    op.create_index("ix_professional_sessions_token_hash", "professional_sessions", ["token_hash"], unique=True)
    op.create_index("ix_professional_sessions_expires_at", "professional_sessions", ["expires_at"], unique=False)

def downgrade():
    op.drop_table("professional_sessions")
    op.drop_table("professional_accounts")
