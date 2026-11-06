"""Create immutable document candidate review decisions.

Revision ID: 20261104_01_doc_review
Revises: 20261102_02_workflow_integrity_merge
"""
from alembic import op
import sqlalchemy as sa

revision = "20261104_01_doc_review"
down_revision = "20261102_02_workflow_integrity_merge"
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
        sa.Column("resulting_question_id", sa.String(), sa.ForeignKey("open_questions.id"), nullable=True),
        sa.Column("superseded_assertion_id", sa.String(), sa.ForeignKey("care_assertions.id"), nullable=True),
        sa.Column("idempotency_key", sa.String(length=128), nullable=True),
        sa.Column("request_sha256", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("analysis_id", "candidate_index", "decision_version", name="uq_document_candidate_review_version"),
        sa.UniqueConstraint("person_id", "idempotency_key", name="uq_document_candidate_review_idempotency"),
        sa.UniqueConstraint("resulting_assertion_id", name="uq_document_candidate_review_assertion"),
    )
    op.create_index("ix_document_candidate_reviews_person_analysis", "document_candidate_reviews", ["person_id", "analysis_id"])
    op.create_index("ix_document_candidate_reviews_analysis_candidate", "document_candidate_reviews", ["analysis_id", "candidate_index", "decision_version"])

    dialect = op.get_bind().dialect.name
    if dialect == "postgresql":
        op.execute("""
            CREATE OR REPLACE FUNCTION reject_document_extraction_source_mutation()
            RETURNS trigger AS $$
            BEGIN
                IF OLD.source_text IS DISTINCT FROM NEW.source_text
                   OR OLD.person_id IS DISTINCT FROM NEW.person_id
                   OR OLD.caregiver_id IS DISTINCT FROM NEW.caregiver_id
                   OR OLD.source_event_id IS DISTINCT FROM NEW.source_event_id
                   OR OLD.source_evidence_id IS DISTINCT FROM NEW.source_evidence_id THEN
                    RAISE EXCEPTION 'persisted document extraction source and identity are immutable';
                END IF;
                RETURN NEW;
            END;
            $$ LANGUAGE plpgsql
        """)
        op.execute("""
            CREATE TRIGGER trg_document_extraction_source_immutable
            BEFORE UPDATE OF source_text, person_id, caregiver_id, source_event_id, source_evidence_id
            ON capture_analyses
            FOR EACH ROW
            WHEN (OLD.candidates->>'kind' = 'document_extraction_v1')
            EXECUTE FUNCTION reject_document_extraction_source_mutation()
        """)
        op.execute("""
            CREATE OR REPLACE FUNCTION reject_evidence_upload_mutation()
            RETURNS trigger AS $$
            BEGIN
                RAISE EXCEPTION 'original uploaded evidence is immutable';
            END;
            $$ LANGUAGE plpgsql
        """)
        op.execute("""
            CREATE TRIGGER trg_evidence_uploads_immutable
            BEFORE UPDATE OF filename, mime_type, content, size_bytes, sha256 ON evidence_uploads
            FOR EACH ROW EXECUTE FUNCTION reject_evidence_upload_mutation()
        """)
        op.execute("""
            CREATE OR REPLACE FUNCTION reject_document_candidate_review_mutation()
            RETURNS trigger AS $$
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
    elif dialect == "sqlite":
        op.execute("""
            CREATE TRIGGER trg_document_extraction_source_immutable
            BEFORE UPDATE OF source_text, person_id, caregiver_id, source_event_id, source_evidence_id
            ON capture_analyses
            WHEN json_extract(OLD.candidates, '$.kind') = 'document_extraction_v1'
                 AND (
                    OLD.source_text IS NOT NEW.source_text
                    OR OLD.person_id IS NOT NEW.person_id
                    OR OLD.caregiver_id IS NOT NEW.caregiver_id
                    OR OLD.source_event_id IS NOT NEW.source_event_id
                    OR OLD.source_evidence_id IS NOT NEW.source_evidence_id
                 )
            BEGIN SELECT RAISE(ABORT, 'persisted document extraction source and identity are immutable'); END
        """)
        op.execute("""
            CREATE TRIGGER trg_evidence_uploads_immutable
            BEFORE UPDATE OF filename, mime_type, content, size_bytes, sha256 ON evidence_uploads
            BEGIN SELECT RAISE(ABORT, 'original uploaded evidence is immutable'); END
        """)
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
        "Document review and source provenance are durable care data; "
        "use a reviewed preservation migration."
    )
