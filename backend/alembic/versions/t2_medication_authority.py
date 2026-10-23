"""Persist trusted medication-source verification records.

Revision ID: t2_med_auth_20261023
Revises: t2_reminders_20261023
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "t2_med_auth_20261023"
down_revision = "t2_reminders_20261023"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    inspector = inspect(bind)
    if not inspector.has_table("medication_authority_verifications"):
        op.create_table(
            "medication_authority_verifications",
            sa.Column("id", sa.String(), primary_key=True),
            sa.Column("evidence_id", sa.String(), sa.ForeignKey("evidence.id"), nullable=False),
            sa.Column("person_id", sa.String(), sa.ForeignKey("persons.id"), nullable=False),
            sa.Column("source_text_sha256", sa.String(length=64), nullable=False),
            sa.Column("authority_role", sa.String(), nullable=False),
            sa.Column("verifier_identity", sa.String(), nullable=False),
            sa.Column("verification_method", sa.String(), nullable=False),
            sa.Column("verified_by_caregiver_id", sa.String(), sa.ForeignKey("caregivers.id"), nullable=False),
            sa.Column("verified_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
            sa.UniqueConstraint("evidence_id", name="uq_medication_authority_evidence"),
        )
    indexes = {i["name"] for i in inspect(bind).get_indexes("medication_authority_verifications")}
    for name, columns in (
        ("ix_medication_authority_verifications_evidence_id", ["evidence_id"]),
        ("ix_medication_authority_verifications_person_id", ["person_id"]),
    ):
        if name not in indexes:
            op.create_index(name, "medication_authority_verifications", columns, unique=False)


def downgrade():
    bind = op.get_bind()
    inspector = inspect(bind)
    if inspector.has_table("medication_authority_verifications"):
        for index in list(inspect(bind).get_indexes("medication_authority_verifications")):
            op.drop_index(index["name"], table_name="medication_authority_verifications")
        op.drop_table("medication_authority_verifications")
