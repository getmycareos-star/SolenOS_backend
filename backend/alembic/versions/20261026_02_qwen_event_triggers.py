"""Add idempotent source-event trigger metadata to Qwen jobs.

Revision ID: 20261026_02_qwen_event_triggers
Revises: 20261026_01_qwen_background_jobs
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "20261026_02_qwen_event_triggers"
down_revision = "20261026_01_qwen_background_jobs"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    if not inspect(bind).has_table("qwen_background_jobs"):
        return
    columns = {column["name"] for column in inspect(bind).get_columns("qwen_background_jobs")}
    if "trigger_key" not in columns:
        op.add_column("qwen_background_jobs", sa.Column("trigger_key", sa.String(length=240), nullable=True))
    if "trigger_source_type" not in columns:
        op.add_column("qwen_background_jobs", sa.Column("trigger_source_type", sa.String(length=48), nullable=True))
    if "trigger_source_id" not in columns:
        op.add_column("qwen_background_jobs", sa.Column("trigger_source_id", sa.String(length=128), nullable=True))
    indexes = {index["name"] for index in inspect(bind).get_indexes("qwen_background_jobs")}
    if "uq_qwen_background_jobs_trigger_key" not in indexes:
        op.create_index("uq_qwen_background_jobs_trigger_key", "qwen_background_jobs", ["trigger_key"], unique=True)


def downgrade():
    bind = op.get_bind()
    if not inspect(bind).has_table("qwen_background_jobs"):
        return
    indexes = {index["name"] for index in inspect(bind).get_indexes("qwen_background_jobs")}
    if "uq_qwen_background_jobs_trigger_key" in indexes:
        op.drop_index("uq_qwen_background_jobs_trigger_key", table_name="qwen_background_jobs")
    columns = {column["name"] for column in inspect(bind).get_columns("qwen_background_jobs")}
    for column in ("trigger_source_id", "trigger_source_type", "trigger_key"):
        if column in columns:
            op.drop_column("qwen_background_jobs", column)
