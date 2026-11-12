"""Track the currently assigned caregiver without rewriting original authorship.

Revision ID: 20261112_08_open_loop_assignment
Revises: 20261112_07_caregiver_access
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "20261112_08_open_loop_assignment"
down_revision = "20261112_07_caregiver_access"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    inspector = inspect(bind)
    if not inspector.has_table("open_loop_records"):
        return
    columns = {item["name"] for item in inspector.get_columns("open_loop_records")}
    if "assigned_caregiver_id" not in columns:
        op.add_column(
            "open_loop_records",
            sa.Column("assigned_caregiver_id", sa.String(), sa.ForeignKey("caregivers.id", ondelete="SET NULL"), nullable=True),
        )
    indexes = {item["name"] for item in inspect(bind).get_indexes("open_loop_records")}
    if "ix_open_loop_records_assigned_caregiver_id" not in indexes:
        op.create_index("ix_open_loop_records_assigned_caregiver_id", "open_loop_records", ["assigned_caregiver_id"])
    op.execute(sa.text("""
        UPDATE open_loop_records
        SET assigned_caregiver_id = caregiver_id
        WHERE assigned_caregiver_id IS NULL
    """))


def downgrade():
    bind = op.get_bind()
    inspector = inspect(bind)
    if not inspector.has_table("open_loop_records"):
        return
    indexes = {item["name"] for item in inspector.get_indexes("open_loop_records")}
    if "ix_open_loop_records_assigned_caregiver_id" in indexes:
        op.drop_index("ix_open_loop_records_assigned_caregiver_id", table_name="open_loop_records")
    columns = {item["name"] for item in inspect(bind).get_columns("open_loop_records")}
    if "assigned_caregiver_id" in columns:
        op.drop_column("open_loop_records", "assigned_caregiver_id")
