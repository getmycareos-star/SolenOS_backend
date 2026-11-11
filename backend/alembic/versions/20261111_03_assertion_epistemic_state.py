"""Separate assertion epistemic state from assertion lifecycle.

Revision ID: 20261111_03_assertion_epistemic_state
Revises: 20261111_02_privacy_exports
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "20261111_03_assertion_epistemic_state"
down_revision = "20261111_02_privacy_exports"
branch_labels = None
depends_on = None

_ALLOWED = ("reported", "observed", "verified", "inferred", "uncertain", "conflicting", "missing")


def upgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    if not inspector.has_table("care_assertions"):
        return
    columns = {column["name"] for column in inspector.get_columns("care_assertions")}
    if "epistemic_state" not in columns:
        op.add_column(
            "care_assertions",
            sa.Column("epistemic_state", sa.String(length=32), nullable=False, server_default="reported"),
        )
    constraints = {item["name"] for item in inspector.get_check_constraints("care_assertions")}
    if "ck_care_assertions_epistemic_state" not in constraints:
        op.create_check_constraint(
            "ck_care_assertions_epistemic_state",
            "care_assertions",
            "epistemic_state IN ('reported', 'observed', 'verified', 'inferred', 'uncertain', 'conflicting', 'missing')",
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    if not inspector.has_table("care_assertions"):
        return
    constraints = {item["name"] for item in inspector.get_check_constraints("care_assertions")}
    if "ck_care_assertions_epistemic_state" in constraints:
        op.drop_constraint("ck_care_assertions_epistemic_state", "care_assertions", type_="check")
    columns = {column["name"] for column in inspector.get_columns("care_assertions")}
    if "epistemic_state" in columns:
        op.drop_column("care_assertions", "epistemic_state")
