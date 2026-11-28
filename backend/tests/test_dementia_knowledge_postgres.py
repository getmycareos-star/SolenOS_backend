"""PostgreSQL integration tests for the governed dementia knowledge lifecycle.

No SQLite fallback or in-memory substitute is used. Tests require the real
PostgreSQL migration schema and clean up only their uniquely scoped fixture rows.
"""
from datetime import date, datetime, timezone
import os
import uuid

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from app.models.dementia_knowledge import (
    DementiaKnowledgeDomain,
    DementiaKnowledgeItem,
    DementiaKnowledgeItemSource,
    DementiaKnowledgeReview,
    DementiaKnowledgeSource,
    DementiaKnowledgeSourceRevision,
    DementiaKnowledgeSourceReview,
)
from app.services.dementia_knowledge import (
    EvidenceClass,
    create_knowledge_item,
    ingest_source_revision,
    retrieve_approved_knowledge,
    review_knowledge_item,
    review_source_revision,
)

DATABASE_URL = os.environ.get("DATABASE_URL", "")
if DATABASE_URL.startswith("postgresql+psycopg://"):
    DATABASE_URL = DATABASE_URL.replace("postgresql+psycopg://", "postgresql+psycopg2://", 1)
pytestmark = pytest.mark.skipif(
    not DATABASE_URL.startswith(("postgresql://", "postgresql+psycopg2://")),
    reason="requires a real PostgreSQL DATABASE_URL and the dementia knowledge migration",
)
AS_OF = date(2026, 11, 28)
NOW = datetime(2026, 11, 28, 12, 0, tzinfo=timezone.utc)


def test_postgres_source_ingestion_review_item_approval_and_companion_retrieval():
    engine = create_engine(DATABASE_URL, pool_pre_ping=True)
    Session = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    suffix = uuid.uuid4().hex
    source_id = f"test-source-{suffix}"
    domain_id = f"test-domain-{suffix}"
    item_id = None
    revision_id = None
    db = Session()
    try:
        db.add(DementiaKnowledgeDomain(
            domain_id=domain_id,
            title="Communication test domain",
            description="PostgreSQL integration fixture domain.",
            display_order=900,
        ))
        db.add(DementiaKnowledgeSource(
            source_id=source_id,
            publisher="Test publisher",
            title="Test caregiver education source",
            canonical_url="https://example.org/dementia-caregiver-education",
            evidence_class="caregiver_education",
            jurisdiction="United States",
            published_on=date(2026, 1, 1),
            publisher_reviewed_on=None,
            status="catalogued_not_ingested",
            scope="Test-only source for the database integration test.",
        ))
        db.flush()

        raw_source = (
            "For a communication interaction, use short clear sentences, allow time "
            "for a response, and reduce competing background noise when practical."
        )
        revision = ingest_source_revision(
            db,
            source_id,
            raw_source,
            retrieved_at=NOW,
            extraction_method="test_fixture_exact_text",
            source_version="test-v1",
        )
        revision_id = revision.id
        same_revision = ingest_source_revision(
            db,
            source_id,
            raw_source,
            retrieved_at=NOW,
            extraction_method="test_fixture_exact_text",
            source_version="test-v1",
        )
        assert same_revision.id == revision.id
        assert revision.review_status == "ingested_not_reviewed"
        assert retrieve_approved_knowledge(
            db, "communication sentences", as_of=AS_OF
        ) == ()

        review_source_revision(
            db,
            revision.id,
            reviewer_id="reviewer-test-001",
            reviewer_qualification="qualified dementia-care content reviewer",
            rationale="Source text and scope checked for this integration test.",
            review_due_on=date(2027, 11, 28),
            decision="approved",
            reviewed_at=NOW,
        )
        item = create_knowledge_item(
            db,
            item_key=f"communication-test-{suffix}",
            domain_id=domain_id,
            title="Clear communication",
            content="Use short clear sentences, allow time for a response, and reduce competing background noise when practical.",
            evidence_class=EvidenceClass.CAREGIVER_EDUCATION,
            jurisdiction="United States",
            applicable_context="General caregiver education; adapt to the person's preferences and situation.",
            limitations="Not a clinical assessment or a substitute for individualized professional advice.",
            source_references=[{
                "source_revision_id": revision.id,
                "supporting_excerpt": "use short clear sentences, allow time for a response",
                "source_locator": "test section 1",
            }],
        )
        item_id = item.id
        assert item.status == "draft"
        assert retrieve_approved_knowledge(
            db, "communication sentences", as_of=AS_OF
        ) == ()

        review_knowledge_item(
            db,
            item.id,
            reviewer_id="reviewer-test-002",
            reviewer_qualification="qualified dementia-care content reviewer",
            rationale="Exact text, scope, source excerpt and applicability reviewed for this integration test.",
            review_due_on=date(2027, 11, 28),
            decision="approved",
            reviewed_at=NOW,
        )
        db.commit()
        db.close()
        db = Session()

        retrieved = retrieve_approved_knowledge(
            db, "communication sentences", as_of=AS_OF
        )
        assert len(retrieved) == 1
        assert retrieved[0]["item_id"] == item_id
        assert retrieved[0]["sources"][0]["source_revision_id"] == revision_id
        assert retrieved[0]["sources"][0]["url"] == "https://example.org/dementia-caregiver-education"

        # A changed item body cannot keep using an approval for the old hash.
        persisted_item = db.get(DementiaKnowledgeItem, item_id)
        persisted_item.content = "Changed after approval without a new review."
        db.commit()
        assert retrieve_approved_knowledge(
            db, "communication changed", as_of=AS_OF
        ) == ()
    finally:
        db.rollback()
        if item_id:
            db.execute(text("DELETE FROM dementia_knowledge_reviews WHERE knowledge_item_id = :id"), {"id": item_id})
            db.execute(text("DELETE FROM dementia_knowledge_item_sources WHERE knowledge_item_id = :id"), {"id": item_id})
            db.execute(text("DELETE FROM dementia_knowledge_items WHERE id = :id"), {"id": item_id})
        if revision_id:
            db.execute(text("DELETE FROM dementia_knowledge_source_reviews WHERE source_revision_id = :id"), {"id": revision_id})
            db.execute(text("DELETE FROM dementia_knowledge_source_revisions WHERE id = :id"), {"id": revision_id})
        db.execute(text("DELETE FROM dementia_knowledge_sources WHERE source_id = :id"), {"id": source_id})
        db.execute(text("DELETE FROM dementia_knowledge_domains WHERE domain_id = :id"), {"id": domain_id})
        db.commit()
        db.close()
        engine.dispose()


def test_postgres_rejects_unreviewed_source_and_missing_exact_excerpt():
    engine = create_engine(DATABASE_URL, pool_pre_ping=True)
    Session = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    suffix = uuid.uuid4().hex
    source_id = f"test-source-reject-{suffix}"
    domain_id = f"test-domain-reject-{suffix}"
    revision_id = None
    db = Session()
    try:
        db.add_all([
            DementiaKnowledgeDomain(
                domain_id=domain_id, title="Test", description="Test domain", display_order=901
            ),
            DementiaKnowledgeSource(
                source_id=source_id, publisher="Test publisher", title="Unreviewed source",
                canonical_url="https://example.org/unreviewed", evidence_class="caregiver_education",
                jurisdiction="United States", status="catalogued_not_ingested",
                scope="Negative test source.",
            ),
        ])
        db.flush()
        revision = ingest_source_revision(
            db, source_id, "The original source says allow time for a response.",
            retrieved_at=NOW, extraction_method="test_fixture_exact_text",
        )
        revision_id = revision.id
        with pytest.raises(ValueError, match="knowledge_source_excerpt_not_found"):
            create_knowledge_item(
                db,
                item_key=f"bad-item-{suffix}",
                domain_id=domain_id,
                title="Bad source link",
                content="An unsupported excerpt.",
                evidence_class=EvidenceClass.CAREGIVER_EDUCATION,
                jurisdiction="United States",
                applicable_context="General education only.",
                limitations="Not clinical advice.",
                source_references=[{
                    "source_revision_id": revision.id,
                    "supporting_excerpt": "This sentence does not occur in the source.",
                }],
            )
        assert revision.review_status == "ingested_not_reviewed"
        assert retrieve_approved_knowledge(db, "response", as_of=AS_OF) == ()
    finally:
        db.rollback()
        if revision_id:
            db.execute(text("DELETE FROM dementia_knowledge_source_revisions WHERE id = :id"), {"id": revision_id})
        db.execute(text("DELETE FROM dementia_knowledge_sources WHERE source_id = :id"), {"id": source_id})
        db.execute(text("DELETE FROM dementia_knowledge_domains WHERE domain_id = :id"), {"id": domain_id})
        db.commit()
        db.close()
        engine.dispose()
