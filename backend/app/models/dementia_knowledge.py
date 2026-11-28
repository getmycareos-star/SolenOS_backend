"""Governed, versioned dementia education knowledge stored in PostgreSQL.

This store is global educational content, not person-specific care evidence and
not part of the LCR. Runtime retrieval is read-only and excludes anything that
has not passed source and item review.
"""
import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    Column, Date, DateTime, ForeignKey, Index, Integer, String, Text,
    UniqueConstraint,
)
from app.core.database import Base


class DementiaKnowledgeDomain(Base):
    __tablename__ = "dementia_knowledge_domains"

    domain_id = Column(String(64), primary_key=True)
    title = Column(String(180), nullable=False)
    description = Column(Text, nullable=False)
    display_order = Column(Integer, nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))


class DementiaKnowledgeSource(Base):
    __tablename__ = "dementia_knowledge_sources"
    __table_args__ = (
        Index("ix_dementia_knowledge_sources_status", "status"),
    )

    source_id = Column(String(80), primary_key=True)
    publisher = Column(String(240), nullable=False)
    title = Column(String(400), nullable=False)
    canonical_url = Column(Text, nullable=False)
    evidence_class = Column(String(48), nullable=False)
    jurisdiction = Column(String(80), nullable=False)
    published_on = Column(Date, nullable=True)
    publisher_reviewed_on = Column(Date, nullable=True)
    status = Column(String(40), nullable=False, default="catalogued_not_ingested")
    scope = Column(Text, nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))


class DementiaKnowledgeSourceRevision(Base):
    __tablename__ = "dementia_knowledge_source_revisions"
    __table_args__ = (
        UniqueConstraint("source_id", "content_sha256", name="uq_dementia_knowledge_source_revision_hash"),
        Index("ix_dementia_knowledge_source_revision_source_time", "source_id", "retrieved_at"),
        Index("ix_dementia_knowledge_source_revision_status", "review_status", "review_due_on"),
    )

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    source_id = Column(String(80), ForeignKey("dementia_knowledge_sources.source_id", ondelete="RESTRICT"), nullable=False)
    source_version = Column(String(120), nullable=True)
    content_text = Column(Text, nullable=False)
    content_sha256 = Column(String(64), nullable=False)
    extraction_method = Column(String(80), nullable=False)
    retrieved_at = Column(DateTime(timezone=True), nullable=False)
    publisher_published_on = Column(Date, nullable=True)
    publisher_reviewed_on = Column(Date, nullable=True)
    review_status = Column(String(40), nullable=False, default="ingested_not_reviewed")
    reviewed_by = Column(String(180), nullable=True)
    reviewer_qualification = Column(String(180), nullable=True)
    reviewed_at = Column(DateTime(timezone=True), nullable=True)
    review_due_on = Column(Date, nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))


class DementiaKnowledgeItem(Base):
    __tablename__ = "dementia_knowledge_items"
    __table_args__ = (
        UniqueConstraint("item_key", "version", name="uq_dementia_knowledge_item_key_version"),
        Index("ix_dementia_knowledge_item_status_domain", "status", "domain_id"),
        Index("ix_dementia_knowledge_item_review_due", "status", "review_due_on"),
    )

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    item_key = Column(String(120), nullable=False)
    version = Column(Integer, nullable=False, default=1)
    domain_id = Column(String(64), ForeignKey("dementia_knowledge_domains.domain_id", ondelete="RESTRICT"), nullable=False)
    title = Column(String(300), nullable=False)
    content = Column(Text, nullable=False)
    content_sha256 = Column(String(64), nullable=False)
    evidence_class = Column(String(48), nullable=False)
    jurisdiction = Column(String(80), nullable=False)
    applicable_context = Column(Text, nullable=False)
    limitations = Column(Text, nullable=False)
    status = Column(String(32), nullable=False, default="draft")
    reviewed_by = Column(String(180), nullable=True)
    reviewer_qualification = Column(String(180), nullable=True)
    reviewed_at = Column(DateTime(timezone=True), nullable=True)
    review_due_on = Column(Date, nullable=True)
    supersedes_item_id = Column(String(36), ForeignKey("dementia_knowledge_items.id", ondelete="RESTRICT"), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))


class DementiaKnowledgeItemSource(Base):
    __tablename__ = "dementia_knowledge_item_sources"
    __table_args__ = (
        UniqueConstraint("knowledge_item_id", "source_revision_id", name="uq_dementia_knowledge_item_source_revision"),
        Index("ix_dementia_knowledge_item_source_item", "knowledge_item_id"),
    )

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    knowledge_item_id = Column(String(36), ForeignKey("dementia_knowledge_items.id", ondelete="CASCADE"), nullable=False)
    source_revision_id = Column(String(36), ForeignKey("dementia_knowledge_source_revisions.id", ondelete="RESTRICT"), nullable=False)
    supporting_excerpt = Column(Text, nullable=False)
    source_locator = Column(String(240), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))


class DementiaKnowledgeReview(Base):
    """Append-only decision record tied to the exact reviewed content hash."""
    __tablename__ = "dementia_knowledge_reviews"
    __table_args__ = (
        Index("ix_dementia_knowledge_reviews_item_time", "knowledge_item_id", "reviewed_at"),
    )

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    knowledge_item_id = Column(String(36), ForeignKey("dementia_knowledge_items.id", ondelete="RESTRICT"), nullable=False)
    content_sha256 = Column(String(64), nullable=False)
    decision = Column(String(24), nullable=False)
    reviewer_id = Column(String(180), nullable=False)
    reviewer_qualification = Column(String(180), nullable=False)
    rationale = Column(Text, nullable=False)
    review_due_on = Column(Date, nullable=True)
    reviewed_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))
