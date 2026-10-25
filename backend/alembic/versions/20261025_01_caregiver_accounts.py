"""Add email/password accounts scoped to a single care record.

Revision ID: 20261025_01
Revises: 20261024_12
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "20261025_01"
down_revision = "20261024_12"
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
    if inspect(bind).has_table("caregiver_accounts"):
        op.drop_table("caregiver_accounts")
