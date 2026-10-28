"""Add event, context and temporal scope to contradiction matching.

Revision ID: 20261028_05_conflict_scope
Revises: 20261028_04_evidence_reviews
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "20261028_05_conflict_scope"
down_revision = "20261028_04_evidence_reviews"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    if not inspector.has_table("contradictions"):
        raise RuntimeError("Cannot scope contradiction matching: contradictions table is required")
    columns = {column["name"] for column in inspector.get_columns("contradictions")}
    if "event_id" not in columns:
        op.add_column("contradictions", sa.Column("event_id", sa.String(), sa.ForeignKey("care_events.id", ondelete="SET NULL"), nullable=True))
    if "context_key" not in columns:
        op.add_column("contradictions", sa.Column("context_key", sa.String(length=160), nullable=True))
    if "effective_from" not in columns:
        op.add_column("contradictions", sa.Column("effective_from", sa.DateTime(timezone=True), nullable=True))
    if "effective_to" not in columns:
        op.add_column("contradictions", sa.Column("effective_to", sa.DateTime(timezone=True), nullable=True))
    if "matching_candidate_ids" not in columns:
        op.add_column("contradictions", sa.Column("matching_candidate_ids", sa.Text(), nullable=False, server_default="[]"))
    columns = {column["name"] for column in inspect(bind).get_columns("contradictions")}
    for cols, name in (
        (["person_id", "subject_type", "subject_id", "field"], "ix_contradictions_match_scope"),
        (["event_id"], "ix_contradictions_event_scope"),
    ):
        # Some historical stamped fixtures only contain the referenced primary
        # key. Add indexes only where the corresponding legacy columns exist.
        if not set(cols).issubset(columns):
            continue
        if not any(index.get("name") == name for index in inspect(bind).get_indexes("contradictions")):
            op.create_index(name, "contradictions", cols, unique=False)


def downgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    if inspector.has_table("contradictions"):
        if bind.execute(sa.text("SELECT 1 FROM contradictions WHERE matching_candidate_ids IS NOT NULL AND matching_candidate_ids <> '[]' LIMIT 1")).first():
            raise RuntimeError("Cannot downgrade while persisted conflict match relationships exist.")
        columns = {column["name"] for column in inspect(bind).get_columns("contradictions")}
        for name in ("ix_contradictions_match_scope", "ix_contradictions_event_scope"):
            if any(index.get("name") == name for index in inspect(bind).get_indexes("contradictions")):
                op.drop_index(name, table_name="contradictions")
        for column in ("matching_candidate_ids", "effective_to", "effective_from", "context_key", "event_id"):
            if column in columns:
                op.drop_column("contradictions", column)
