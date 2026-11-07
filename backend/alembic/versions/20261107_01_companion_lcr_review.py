"""Add source lineage and review integrity for Companion LCR proposals.

Revision ID: 20261107_01_companion_lcr_review
Revises: 20261102_02_workflow_integrity_merge
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "20261107_01_companion_lcr_review"
down_revision = "20261102_02_workflow_integrity_merge"
branch_labels = None
depends_on = None


def _columns(bind, table):
    if not inspect(bind).has_table(table):
        raise RuntimeError(f"Required existing table is missing: {table}")
    return {column["name"] for column in inspect(bind).get_columns(table)}


def upgrade():
    bind = op.get_bind()
    if not inspect(bind).has_table("ai_output_lcr_proposals"):
        raise RuntimeError("Shared AIOutputLCRProposal governance table must exist")
    if not inspect(bind).has_table("ai_output_lcr_decisions"):
        raise RuntimeError("Shared AIOutputLCRDecision governance table must exist")
    if not inspect(bind).has_table("care_assertions"):
        raise RuntimeError("Authoritative CareAssertion table must exist")
    if not inspect(bind).has_table("conversation_sessions") or not inspect(bind).has_table("conversation_messages"):
        raise RuntimeError("Persisted Companion conversation tables must exist")

    proposal_columns = _columns(bind, "ai_output_lcr_proposals")
    for column in (
        sa.Column("source_conversation_id", sa.String(), sa.ForeignKey("conversation_sessions.id", ondelete="SET NULL"), nullable=True),
        sa.Column("source_message_id", sa.String(), sa.ForeignKey("conversation_messages.id", ondelete="SET NULL"), nullable=True),
        sa.Column("candidate_index", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("proposal_version", sa.Integer(), nullable=False, server_default="0"),
    ):
        if column.name not in proposal_columns:
            op.add_column("ai_output_lcr_proposals", column)

    assertion_columns = _columns(bind, "care_assertions")
    for column in (
        sa.Column("source_conversation_id", sa.String(), sa.ForeignKey("conversation_sessions.id", ondelete="SET NULL"), nullable=True),
        sa.Column("source_message_id", sa.String(), sa.ForeignKey("conversation_messages.id", ondelete="SET NULL"), nullable=True),
        sa.Column("source_proposal_id", sa.String(), sa.ForeignKey("ai_output_lcr_proposals.id", ondelete="SET NULL"), nullable=True),
        sa.Column("event_time_text", sa.Text(), nullable=True),
    ):
        if column.name not in assertion_columns:
            op.add_column("care_assertions", column)

    indexes = {item["name"] for item in inspect(bind).get_indexes("ai_output_lcr_proposals")}
    if "uq_ai_output_lcr_proposals_source_version" not in indexes:
        op.create_index(
            "uq_ai_output_lcr_proposals_source_version",
            "ai_output_lcr_proposals",
            ["source_message_id", "candidate_index", "proposal_version"],
            unique=True,
        )
    if "ix_ai_output_lcr_proposals_source_conversation" not in indexes:
        op.create_index("ix_ai_output_lcr_proposals_source_conversation", "ai_output_lcr_proposals", ["source_conversation_id"])

    assertion_indexes = {item["name"] for item in inspect(bind).get_indexes("care_assertions")}
    if "uq_care_assertions_source_proposal" not in assertion_indexes:
        op.create_index("uq_care_assertions_source_proposal", "care_assertions", ["source_proposal_id"], unique=True)
    if "ix_care_assertions_source_message" not in assertion_indexes:
        op.create_index("ix_care_assertions_source_message", "care_assertions", ["source_message_id"])

    if bind.dialect.name == "postgresql":
        op.execute("""
            CREATE OR REPLACE FUNCTION reject_ai_output_lcr_decision_mutation() RETURNS trigger AS $companion_decision$
            BEGIN
                RAISE EXCEPTION 'AI output LCR decision history is append-only';
            END;
            $companion_decision$ LANGUAGE plpgsql
        """)
        op.execute("DROP TRIGGER IF EXISTS trg_ai_output_lcr_decisions_immutable ON ai_output_lcr_decisions")
        op.execute("""
            CREATE TRIGGER trg_ai_output_lcr_decisions_immutable
            BEFORE UPDATE OR DELETE ON ai_output_lcr_decisions
            FOR EACH ROW EXECUTE FUNCTION reject_ai_output_lcr_decision_mutation()
        """)
    elif bind.dialect.name == "sqlite":
        op.execute("""
            CREATE TRIGGER IF NOT EXISTS trg_ai_output_lcr_decisions_no_update
            BEFORE UPDATE ON ai_output_lcr_decisions
            BEGIN SELECT RAISE(ABORT, 'AI output LCR decision history is append-only'); END
        """)
        op.execute("""
            CREATE TRIGGER IF NOT EXISTS trg_ai_output_lcr_decisions_no_delete
            BEFORE DELETE ON ai_output_lcr_decisions
            BEGIN SELECT RAISE(ABORT, 'AI output LCR decision history is append-only'); END
        """)


def downgrade():
    raise RuntimeError("Companion source lineage and review decisions are retained; use a reviewed data-preservation migration.")
