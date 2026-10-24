"""Persist caregiver consent and privacy preferences.

Revision ID: 20261024_10
Revises: 20261024_09
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "20261024_10"
down_revision = "20261024_09"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    if not inspect(bind).has_table("caregiver_consents"):
        op.create_table(
            "caregiver_consents",
            sa.Column("id", sa.String(), primary_key=True),
            sa.Column("person_id", sa.String(), sa.ForeignKey("persons.id"), nullable=False),
            sa.Column("caregiver_id", sa.String(), sa.ForeignKey("caregivers.id"), nullable=False),
            sa.Column("medical_disclaimer_acknowledged", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("privacy_model_acknowledged", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("multi_caregiver_acknowledged", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("no_advertising_acknowledged", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("data_improvement_consent", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("accepted_terms_version", sa.String(), nullable=False, server_default="2026-10"),
            sa.Column("accepted_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
            sa.UniqueConstraint("person_id", "caregiver_id", name="uq_caregiver_consent_person_actor"),
        )
    inspector = inspect(bind)
    if inspector.has_table("caregiver_consents"):
        existing = {item["name"] for item in inspector.get_indexes("caregiver_consents")}
        for name, columns in [
            ("ix_caregiver_consents_person_id", ["person_id"]),
            ("ix_caregiver_consents_caregiver_id", ["caregiver_id"]),
        ]:
            if name not in existing:
                op.create_index(name, "caregiver_consents", columns)


def downgrade():
    bind = op.get_bind()
    if inspect(bind).has_table("caregiver_consents"):
        op.drop_table("caregiver_consents")
