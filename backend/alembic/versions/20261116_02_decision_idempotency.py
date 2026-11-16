"""Add person-scoped idempotency keys for captured care decisions."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "20261116_02_decision_idempotency"
down_revision = "20261116_01_followup_owner"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    inspector = inspect(bind)
    if not inspector.has_table("care_decisions"):
        raise RuntimeError("Cannot add decision idempotency: care_decisions is missing")
    columns = {column["name"] for column in inspector.get_columns("care_decisions")}
    if "client_reference_id" not in columns:
        op.add_column(
            "care_decisions",
            sa.Column("client_reference_id", sa.String(120), nullable=True),
        )
    indexes = {index["name"] for index in inspect(bind).get_indexes("care_decisions")}
    if "uq_care_decisions_person_client_reference" not in indexes:
        op.create_index(
            "uq_care_decisions_person_client_reference",
            "care_decisions",
            ["person_id", "client_reference_id"],
            unique=True,
        )


def downgrade():
    # Preserve idempotency keys and their uniqueness protection on downgrade.
    pass
