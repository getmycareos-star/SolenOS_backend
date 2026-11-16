"""Add person-scoped idempotency keys for decision outcome capture."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "20261116_03_decision_outcome_idempotency"
down_revision = "20261116_02_decision_idempotency"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    inspector = inspect(bind)
    if not inspector.has_table("decision_outcomes"):
        raise RuntimeError("Cannot add outcome idempotency: decision_outcomes is missing")
    columns = {column["name"] for column in inspector.get_columns("decision_outcomes")}
    if "client_reference_id" not in columns:
        op.add_column(
            "decision_outcomes",
            sa.Column("client_reference_id", sa.String(120), nullable=True),
        )
    indexes = {index["name"] for index in inspect(bind).get_indexes("decision_outcomes")}
    if "uq_decision_outcomes_person_client_reference" not in indexes:
        op.create_index(
            "uq_decision_outcomes_person_client_reference",
            "decision_outcomes",
            ["person_id", "client_reference_id"],
            unique=True,
        )


def downgrade():
    # Preserve outcome idempotency keys and their uniqueness protection.
    pass
