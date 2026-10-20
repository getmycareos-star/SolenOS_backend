from sqlalchemy import Column, String, DateTime, Text, Integer, ForeignKey, Index, Boolean
from sqlalchemy.sql import func
import uuid
from app.core.database import Base
from app.core.types import JSONDict, JSONList
from app.models.identity import Person, Caregiver, Account


class FlexibleDocument(Base):
    """Flexible document store for unstructured or semi-structured data.
    
    This provides document store capabilities alongside the relational model,
    allowing storage of arbitrary JSON data while still maintaining queryability
    and relationships to core entities like persons.
    """
    __tablename__ = "flexible_documents"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    person_id = Column(String, ForeignKey("persons.id"), nullable=True, index=True)
    caregiver_id = Column(String, ForeignKey("caregivers.id"), nullable=True, index=True)
    
    # Document metadata
    document_type = Column(String, nullable=False, index=True)  # e.g., "notes", "report", "image_analysis", "audio_transcript"
    subtype = Column(String, nullable=True, index=True)  # Further categorization
    title = Column(String, nullable=True)
    description = Column(Text, nullable=True)
    
    # Content storage - the hybrid aspect
    # Structured data that fits in columns
    content_text = Column(Text, nullable=True)
    content_json = Column(JSONDict, nullable=True)  # For structured JSON data
    content_blob = Column(String, nullable=True)  # Reference to external blob storage
    
    # Flexible schema - arbitrary attributes
    attributes = Column(JSONDict, nullable=True)  # Arbitrary key-value pairs
    tags = Column(JSONList, nullable=True)  # Tagging for categorization
    
    # Processing and provenance
    processing_status = Column(String, nullable=False, default="pending")
    processing_error = Column(Text, nullable=True)
    source_type = Column(String, nullable=False, default="manual")  # manual, automated, imported
    source_id = Column(String, nullable=True)  # ID from source system
    
    # File attachment (optional)
    file_name = Column(String, nullable=True)
    file_size = Column(Integer, nullable=True)
    content_hash = Column(String, nullable=True)
    
    # Access control and visibility
    is_public = Column(Boolean, nullable=False, default=False)
    is_restricted = Column(Boolean, nullable=False, default=False)
    access_level = Column(String, nullable=False, default="private")  # private, restricted, team, public
    
    # Timestamps
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())
    created_by = Column(String, ForeignKey("accounts.id"), nullable=True)
    
    # Versioning
    version = Column(Integer, nullable=False, default=1)
    parent_id = Column(String, ForeignKey("flexible_documents.id"), nullable=True)
    
    # Indexes for query performance
    __table_args__ = (
        Index('ix_flexible_docs_person_type', 'person_id', 'document_type'),
        Index('ix_flexible_docs_caregiver_type', 'caregiver_id', 'document_type'),
        Index('ix_flexible_docs_type_subtype', 'document_type', 'subtype'),
        Index('ix_flexible_docs_created_at', 'created_at'),
    )

    # Relationships
    person = relationship("Person", foreign_keys=[person_id])
    caregiver = relationship("Caregiver", foreign_keys=[caregiver_id])
    creator = relationship("Account", foreign_keys=[created_by])
    parent = relationship(
        "FlexibleDocument",
        remote_side=[id],
        foreign_keys=[parent_id],
        back_populates="children",
    )
    children = relationship(
        "FlexibleDocument",
        foreign_keys=[parent_id],
        back_populates="parent",
    )
