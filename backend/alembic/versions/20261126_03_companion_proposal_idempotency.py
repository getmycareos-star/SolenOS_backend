"""Enforce one Companion care proposal per source message and version.

Revision ID: 20261126_03_companion_proposal_idempotency
Revises: 20261126_02_companion_conversation_permission
"""
from alembic import op
from sqlalchemy import inspect, text

revision = "20261126_03_companion_proposal_idempotency"
down_revision = "20261126_02_companion_conversation_permission"
branch_labels = None
depends_on = None

INDEX_NAME = "uq_companion_care_proposal_source_version"


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        raise RuntimeError("Companion proposal idempotency requires PostgreSQL.")
    inspector = inspect(bind)
    if not inspector.has_table("ai_output_lcr_proposals"):
        raise RuntimeError("Cannot enforce Companion proposal idempotency; proposal table is missing.")
    columns = {column["name"] for column in inspector.get_columns("ai_output_lcr_proposals")}
    required = {
        "person_id", "caregiver_id", "source_message_id", "candidate_index",
        "proposal_version", "proposal_type",
    }
    if not required.issubset(columns):
        raise RuntimeError("Companion proposal schema is incomplete.")

    duplicates = bind.execute(text("""
        SELECT person_id, caregiver_id, source_message_id,
               candidate_index, proposal_version, count(*) AS duplicate_count
        FROM ai_output_lcr_proposals
        WHERE proposal_type = 'companion_care_observation'
          AND source_message_id IS NOT NULL
        GROUP BY person_id, caregiver_id, source_message_id,
                 candidate_index, proposal_version
        HAVING count(*) > 1
        LIMIT 10
    """)).mappings().all()
    if duplicates:
        raise RuntimeError(
            "Companion proposal idempotency migration found duplicate source versions. "
            "Resolve these rows without deleting review history before retrying: "
            + "; ".join(str(dict(row)) for row in duplicates)
        )

    indexes = {index["name"] for index in inspector.get_indexes("ai_output_lcr_proposals")}
    if INDEX_NAME not in indexes:
        op.create_index(
            INDEX_NAME,
            "ai_output_lcr_proposals",
            ["person_id", "caregiver_id", "source_message_id", "candidate_index", "proposal_version"],
            unique=True,
            postgresql_where=text(
                "proposal_type = 'companion_care_observation' AND source_message_id IS NOT NULL"
            ),
        )


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return
    inspector = inspect(bind)
    if inspector.has_table("ai_output_lcr_proposals"):
        indexes = {index["name"] for index in inspector.get_indexes("ai_output_lcr_proposals")}
        if INDEX_NAME in indexes:
            op.drop_index(INDEX_NAME, table_name="ai_output_lcr_proposals")
