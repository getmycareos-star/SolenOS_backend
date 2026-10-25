"""Add caregiver email/password account credentials.

Revision ID: 20261024_11
Revises: 20261024_10
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "20261024_11"
down_revision = "20261024_10"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    if not inspect(bind).has_table("caregiver_accounts"):
        op.create_table(
            "caregiver_accounts",
            sa.Column("id", sa.String(), primary_key=True, nullable=False),
            sa.Column("email", sa.String(length=254), nullable=False),
            sa.Column("password_hash", sa.String(length=255), nullable=False),
            sa.Column("person_id", sa.String(), sa.ForeignKey("persons.id"), nullable=False),
            sa.Column("caregiver_id", sa.String(), sa.ForeignKey("caregivers.id"), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
            sa.UniqueConstraint("email", name="uq_caregiver_accounts_email"),
            sa.UniqueConstraint("person_id", name="uq_caregiver_accounts_person"),
            sa.UniqueConstraint("caregiver_id", name="uq_caregiver_accounts_caregiver"),
        )
    indexes = {item["name"] for item in inspect(bind).get_indexes("caregiver_accounts")}
    if "ix_caregiver_accounts_email" not in indexes:
        op.create_index("ix_caregiver_accounts_email", "caregiver_accounts", ["email"], unique=True)
    if "ix_caregiver_accounts_person_id" not in indexes:
        op.create_index("ix_caregiver_accounts_person_id", "caregiver_accounts", ["person_id"], unique=True)
    if "ix_caregiver_accounts_caregiver_id" not in indexes:
        op.create_index("ix_caregiver_accounts_caregiver_id", "caregiver_accounts", ["caregiver_id"], unique=True)


def downgrade():
    bind = op.get_bind()
    if inspect(bind).has_table("caregiver_accounts"):
        op.drop_table("caregiver_accounts")
