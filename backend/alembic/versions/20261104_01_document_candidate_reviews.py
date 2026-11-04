"""Create immutable document candidate review decisions.

Revision ID: 20261104_01_document_candidate_reviews
Revises: 20261102_01_explicit_actions
"""
from alembic import op
import sqlalchemy as sa

revision = "20261104_01_doc_review"
down_revision = "20261102_01_explicit_actions"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "document_candidate_reviews",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("analysis_id", sa.String(), sa.ForeignKey("capture_analyses.id", ondelete="CASCADE"), nullable=False),
        sa.Column("person_id", sa.String(), sa.ForeignKey("persons.id", ondelete="CASCADE"), nullable=False),
        sa.Column("caregiver_id", sa.String(), sa.ForeignKey("caregivers.id"), nullable=False),
        sa.Column("source_evidence_id", sa.String(), sa.ForeignKey("evidence.id", ondelete="CASCADE"), nullable=False),
        sa.Column("candidate_index", sa.Integer(), nullable=False),
        sa.Column("decision_version", sa.Integer(), nullable=False),
        sa.Column("decision", sa.String(length=24), nullable=False),
        sa.Column("original_candidate", sa.JSON(), nullable=False),
        sa.Column("source_quote", sa.Text(), nullable=False),
        sa.Column("corrected_subject", sa.String(length=160), nullable=True),
        sa.Column("corrected_observation", sa.Text(), nullable=True),
        sa.Column("rationale", sa.Text(), nullable=True),
        sa.Column("resulting_assertion_id", sa.String(), sa.ForeignKey("care_assertions.id"), nullable=True),
        sa.Column("superseded_assertion_id", sa.String(), sa.ForeignKey("care_assertions.id"), nullable=True),
        sa.Column("idempotency_key", sa.String(length=128), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("analysis_id", "candidate_index", "decision_version", name="uq_document_candidate_review_version"),
        sa.UniqueConstraint("person_id", "idempotency_key", name="uq_document_candidate_review_idempotency"),
        sa.UniqueConstraint("resulting_assertion_id", name="uq_document_candidate_review_assertion"),
    )
    op.create_index("ix_document_candidate_reviews_person_analysis", "document_candidate_reviews", ["person_id", "analysis_id"])
    op.create_index("ix_document_candidate_reviews_analysis_candidate", "document_candidate_reviews", ["analysis_id", "candidate_index", "decision_version"])
    if op.get_bind().dialect.name == "postgresql":
        op.execute("""
            CREATE OR REPLACE FUNCTION reject_document_candidate_review_mutation() RETURNS trigger AS $$
            BEGIN
                RAISE EXCEPTION 'document candidate review history is append-only';
            END;
            $$ LANGUAGE plpgsql
        """)
        op.execute("""
            CREATE TRIGGER trg_document_candidate_reviews_immutable
            BEFORE UPDATE OR DELETE ON document_candidate_reviews
            FOR EACH ROW EXECUTE FUNCTION reject_document_candidate_review_mutation()
        """)
    elif op.get_bind().dialect.name == "sqlite":
        op.execute("""
            CREATE TRIGGER trg_document_candidate_reviews_no_update
            BEFORE UPDATE ON document_candidate_reviews
            BEGIN SELECT RAISE(ABORT, 'document candidate review history is append-only'); END
        """)
        op.execute("""
            CREATE TRIGGER trg_document_candidate_reviews_no_delete
            BEFORE DELETE ON document_candidate_reviews
            BEGIN SELECT RAISE(ABORT, 'document candidate review history is append-only'); END
        """)


def downgrade() -> None:
    raise RuntimeError(
        "Document candidate review history is durable care data; "
        "use a reviewed preservation migration."
    )
