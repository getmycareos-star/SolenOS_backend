"""Persist action completion time and the authenticated caregiver attribution.

Revision ID: t2_completion_20261023
Revises: 20261023_05
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "t2_completion_20261023"
down_revision = "20261023_05"
branch_labels = None
depends_on = None

FK_NAME = "fk_actions_completed_by_caregiver_id_caregivers"
INDEX_NAME = "ix_actions_completed_by_caregiver_id"


def upgrade():
    bind = op.get_bind()
    inspector = inspect(bind)
    if not inspector.has_table("actions"):
        raise RuntimeError("Cannot add completion attribution: actions table is missing")

    columns = {column["name"] for column in inspector.get_columns("actions")}
    if "completed_at" not in columns:
        op.add_column("actions", sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True))
    if "completed_by_caregiver_id" not in columns:
        op.add_column("actions", sa.Column("completed_by_caregiver_id", sa.String(), nullable=True))

    inspector = inspect(bind)
    foreign_keys = inspector.get_foreign_keys("actions")
    has_completion_fk = any(
        fk.get("referred_table") == "caregivers"
        and fk.get("constrained_columns") == ["completed_by_caregiver_id"]
        for fk in foreign_keys
    )
    if not has_completion_fk:
        with op.batch_alter_table("actions") as batch_op:
            batch_op.create_foreign_key(
                FK_NAME,
                "caregivers",
                ["completed_by_caregiver_id"],
                ["id"],
            )

    inspector = inspect(bind)
    indexes = {index["name"] for index in inspector.get_indexes("actions")}
    if INDEX_NAME not in indexes:
        op.create_index(INDEX_NAME, "actions", ["completed_by_caregiver_id"], unique=False)


def downgrade():
    bind = op.get_bind()
    inspector = inspect(bind)
    if not inspector.has_table("actions"):
        return

    indexes = {index["name"] for index in inspector.get_indexes("actions")}
    if INDEX_NAME in indexes:
        op.drop_index(INDEX_NAME, table_name="actions")

    inspector = inspect(bind)
    foreign_keys = inspector.get_foreign_keys("actions")
    completion_fk = next((
        fk for fk in foreign_keys
        if fk.get("name") == FK_NAME
        or (fk.get("referred_table") == "caregivers"
            and fk.get("constrained_columns") == ["completed_by_caregiver_id"])
    ), None)
    if completion_fk:
        with op.batch_alter_table("actions") as batch_op:
            batch_op.drop_constraint(FK_NAME, type_="foreignkey")

    inspector = inspect(bind)
    columns = {column["name"] for column in inspector.get_columns("actions")}
    if "completed_by_caregiver_id" in columns:
        op.drop_column("actions", "completed_by_caregiver_id")
    if "completed_at" in columns:
        op.drop_column("actions", "completed_at")
