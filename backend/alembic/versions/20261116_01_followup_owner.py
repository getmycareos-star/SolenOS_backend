"""Add explicit caregiver ownership to durable care follow-ups."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "20261116_01_followup_owner"
down_revision = "20261115_06_repair_caregiver_responsibility_transfers"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    inspector = inspect(bind)
    if not inspector.has_table("care_follow_up_links"):
        raise RuntimeError("Cannot add follow-up ownership: care_follow_up_links is missing")
    columns = {column["name"] for column in inspector.get_columns("care_follow_up_links")}
    if "owner_caregiver_id" not in columns:
        op.add_column(
            "care_follow_up_links",
            sa.Column(
                "owner_caregiver_id",
                sa.String(36),
                sa.ForeignKey("caregivers.id", ondelete="SET NULL"),
                nullable=True,
            ),
        )
    indexes = {index["name"] for index in inspect(bind).get_indexes("care_follow_up_links")}
    if "ix_care_follow_up_links_person_owner_status" not in indexes:
        op.create_index(
            "ix_care_follow_up_links_person_owner_status",
            "care_follow_up_links",
            ["person_id", "owner_caregiver_id", "status"],
            unique=False,
        )


def downgrade():
    # Preserve assigned ownership on downgrade; no destructive reversal.
    pass
