from typing import Optional, Tuple
from sqlalchemy.orm import Session
from sqlalchemy import Column, String, DateTime, Text, ForeignKey, Enum, Integer, Boolean, Index, Table, UniqueConstraint
import hashlib
import json
from datetime import datetime, timezone
from sqlalchemy.sql import func
import uuid
from app.core.database import Base
from app.core.types import JSONList, JSONDict
import enum


class CommunityContentType(str, enum.Enum):
    QUESTION = 'question'
    DISCUSSION = 'discussion'
    CAREGIVER_EXPERIENCE = 'caregiver_experience'
    PROFESSIONAL_EDUCATION = 'professional_education'
    RESOURCE = 'resource'
    ANNOUNCEMENT = 'announcement'
    SUPPORT = 'support'


class CommunityAuthorType(str, enum.Enum):
    PEER_CAREGIVER = 'peer_caregiver'
    VERIFIED_PROFESSIONAL = 'verified_professional'
    PROFESSIONAL = 'professional'  # Unverified professional
    SOLANOS_TEAM = 'solenos_team'


class CommunityPostStatus(str, enum.Enum):
    DRAFT = 'draft'
    PENDING_MODERATION = 'pending_moderation'
    APPROVED = 'approved'
    FLAGGED = 'flagged'
    ARCHIVED = 'archived'


class CommunityVisibility(str, enum.Enum):
    PUBLIC = 'public'
    PRIVATE = 'private'
    TEAM_ONLY = 'team_only'


class QuestionStatus(str, enum.Enum):
    OPEN = 'open'
    RESOLVED = 'resolved'
    EXPIRED = 'expired'


class ReactionType(str, enum.Enum):
    LOVE = 'love'
    SUPPORT = 'support'
    APPRECIATE = 'appreciate'


class ModerationAction(str, enum.Enum):
    HIDE = 'hide'
    REMOVE = 'remove'
    RESTORE = 'restore'
    LOCK = 'lock'
    RESTRICT_USER = 'restrict_user'
    SUSPEND_USER = 'suspend_user'
    WARN = 'warn'
    ESCALATE = 'escalate'


class VerificationStatus(str, enum.Enum):
    SUBMITTED = 'submitted'
    PENDING = 'pending'
    UNDER_REVIEW = 'under_review'
    VERIFIED = 'verified'
    REJECTED = 'rejected'
    REQUIRES_MORE_INFORMATION = 'requires_more_information'
    EXPIRING = 'expiring'
    REQUIRES_REVERIFICATION = 'requires_reverification'
    SUSPENDED = 'suspended'
    EXPIRED = 'expired'


DEFAULT_RELEVANCE_TTL_HOURS = 168


# Association table for LCR-Community references
lcr_community_reference = Table(
    'lcr_community_references',
    Base.metadata,
    Column('id', String, primary_key=True, default=lambda: str(uuid.uuid4())),
    Column('lcr_id', String, ForeignKey('persons.id'), nullable=False, index=True),
    Column('community_post_id', String, ForeignKey('community_posts.id'), nullable=False, index=True),
    Column('saved_by_user_id', String, ForeignKey('accounts.id'), nullable=False, index=True),
    Column('reference_type', String, nullable=False),  # REFERENCE, CONTEXT, QUESTION, NOTE, RESOURCE, EXPERIENCE, PROFESSIONAL_INFORMATION
    Column('created_at', DateTime(timezone=True), server_default=func.now()),
    Column('updated_at', DateTime(timezone=True), onupdate=func.now()),
    Column('snapshot_body', Text, nullable=True),  # Snapshot of content at time of reference
    Column('snapshot_created_at', DateTime(timezone=True), nullable=True),
    Column('snapshot_reason', String, nullable=True),
    UniqueConstraint('lcr_id', 'community_post_id', 'saved_by_user_id', name='uq_lcr_community_reference')
)


class CommunityPost(Base):
    __tablename__ = 'community_posts'
    
    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    account_id = Column(String, ForeignKey('accounts.id'), nullable=False, index=True)
    person_id = Column(String, ForeignKey('persons.id'), nullable=False, index=True)
    
    # Author information - separates authentication identity from role/epistemic status
    author_type = Column(Enum(CommunityAuthorType), nullable=False, default=CommunityAuthorType.PEER_CAREGIVER)
    author_label = Column(String, nullable=False)  # Display name for the author
    author_avatar_seed = Column(String, nullable=False)  # For avatar generation
    
    # Professional verification information (when applicable)
    professional_profile_id = Column(String, ForeignKey('professional_profiles.id'), nullable=True, index=True)
    verification_status = Column(Enum(VerificationStatus), nullable=True)
    
    # Content classification
    content_type = Column(Enum(CommunityContentType), nullable=False, default=CommunityContentType.CAREGIVER_EXPERIENCE)
    title = Column(Text, nullable=True)
    body = Column(Text, nullable=False)
    media = Column(JSONList, nullable=True)  # List of media attachments
    
    # Content metadata
    visibility = Column(Enum(CommunityVisibility), nullable=False, default=CommunityVisibility.PUBLIC)
    care_stage = Column(String, nullable=True)  # Flexible staging (early/moderate/late/etc.)
    tags = Column(JSONList, nullable=True)
    
    # Post lifecycle and moderation
    status = Column(Enum(CommunityPostStatus), nullable=False, default=CommunityPostStatus.PENDING_MODERATION)
    moderation_reason = Column(Text, nullable=True)
    moderation_action_by = Column(String, ForeignKey('accounts.id'), nullable=True, index=True)  # Who took action
    moderation_action_at = Column(DateTime(timezone=True), nullable=True)
    
    # Question-specific fields
    question_status = Column(Enum(QuestionStatus), nullable=True)
    expires_at = Column(DateTime(timezone=True), nullable=True)  # For question expiration
    
    # Engagement metrics (non-authoritative signals)
    helpful_marks = Column(Integer, nullable=False, default=0)
    
    # Provenance and audit trail
    provenance_source = Column(Text, nullable=True)  # How this post originated
    provenance_note = Column(Text, nullable=True)  # Additional provenance notes
    edited_at = Column(DateTime(timezone=True), nullable=True)
    edited_by = Column(String, ForeignKey('accounts.id'), nullable=True, index=True)
    
    # Timestamps
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())
    
    # Relationships (commented out as they are defined in identity model)
    # professional_profile = relationship('ProfessionalProfile')  # Defined in identity model
    # moderator_account = relationship('Account', foreign_keys=[moderation_action_by])
    # editor_account = relationship('Account', foreign_keys=[edited_by])


class CommunityReaction(Base):
    __tablename__ = 'community_reactions'
    
    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    post_id = Column(String, ForeignKey('community_posts.id'), nullable=False, index=True)
    account_id = Column(String, ForeignKey('accounts.id'), nullable=False, index=True)
    
    # Author information for the reaction
    author_type = Column(Enum(CommunityAuthorType), nullable=False, default=CommunityAuthorType.PEER_CAREGIVER)
    reaction_type = Column(Enum(ReactionType), nullable=False)
    
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    
    # Relationships
    # post = relationship('CommunityPost', back_populates='reactions')
    # reacting_account = relationship('Account')


class CommunityReport(Base):
    __tablename__ = 'community_reports'
    
    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    reporter_id = Column(String, ForeignKey('accounts.id'), nullable=False, index=True)
    post_id = Column(String, ForeignKey('community_posts.id'), nullable=False, index=True)
    
    # Report details
    reason = Column(String, nullable=False)  # Should align with ModerationReason or similar
    description = Column(Text, nullable=True)
    
    # Report lifecycle
    status = Column(String, nullable=False, default='submitted')  # submitted, triaged, under_review, actioned, resolved
    action_taken = Column(String, nullable=True)  # References ModerationAction
    action_taken_by = Column(String, ForeignKey('accounts.id'), nullable=True, index=True)
    action_taken_at = Column(DateTime(timezone=True), nullable=True)
    
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())
    
    # Relationships
    # reporting_account = relationship('Account', foreign_keys=[reporter_id])
    # reported_post = relationship('CommunityPost')
    # action_account = relationship('Account', foreign_keys=[action_taken_by])


class CommunityModerationAudit(Base):
    __tablename__ = 'community_moderation_audit'
    
    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    post_id = Column(String, ForeignKey('community_posts.id'), nullable=False, index=True)
    moderator_id = Column(String, ForeignKey('accounts.id'), nullable=False, index=True)
    
    action = Column(Enum(ModerationAction), nullable=False)
    reason = Column(Text, nullable=True)
    
    # State tracking
    previous_status = Column(String, nullable=True)
    new_status = Column(String, nullable=True)
    
    # Tamper-proofing fields
    content_hash = Column(String, nullable=False)  # Hash of the record's content
    previous_hash = Column(String, nullable=True)  # Hash of the previous record in chain
    
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    
    # Relationships
    # post = relationship('CommunityPost')
    # moderator_account = relationship('Account')
    
    def compute_content_hash(self) -> str:
        """Compute hash of the record's content for tamper-proofing."""
        # Create a deterministic string representation of the record's content
        content_dict = {
            'id': self.id,
            'post_id': self.post_id,
            'moderator_id': self.moderator_id,
            'action': self.action.value if self.action else None,
            'reason': self.reason,
            'previous_status': self.previous_status,
            'new_status': self.new_status,
            'created_at': self.created_at.isoformat() if self.created_at else None
        }
        
        # Sort keys for consistent hashing
        content_json = json.dumps(content_dict, sort_keys=True, default=str)
        return hashlib.sha256(content_json.encode('utf-8')).hexdigest()
    
    def verify_integrity(self) -> bool:
        """Verify that the record's content hash matches the stored hash."""
        computed_hash = self.compute_content_hash()
        return computed_hash == self.content_hash
    
    @staticmethod
    def get_latest_hash(db: Session) -> Optional[str]:
        """Get the hash of the most recent audit record for chaining."""
        latest_record = db.query(CommunityModerationAudit).order_by(
            CommunityModerationAudit.created_at.desc()
        ).first()
        return latest_record.content_hash if latest_record else None
    
    @staticmethod
    def verify_chain_integrity(db: Session) -> Tuple[bool, Optional[str]]:
        """
        Verify the integrity of the entire audit chain.
        
        Returns:
            Tuple of (is_valid, first_invalid_record_id)
            is_valid: True if all records in the chain are valid and properly linked
            first_invalid_record_id: ID of the first invalid record, if any
        """
        # Get all records ordered by creation time (oldest first)
        records = db.query(CommunityModerationAudit).order_by(
            CommunityModerationAudit.created_at.asc()
        ).all()
        
        if not records:
            return True, None  # Empty chain is valid
        
        # Verify each record's content hash and chain linkage
        expected_previous_hash = None
        for record in records:
            # Verify the record's content hash
            if not record.verify_integrity():
                return False, record.id
            
            # Verify the chain linkage (except for the first record)
            if record.previous_hash != expected_previous_hash:
                return False, record.id
            
            # Update expected hash for next iteration
            expected_previous_hash = record.content_hash
        
        return True, None


class CommunitySavedItem(Base):
    __tablename__ = 'community_saved_items'
    
    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    account_id = Column(String, ForeignKey('accounts.id'), nullable=False, index=True)
    post_id = Column(String, ForeignKey('community_posts.id'), nullable=False, index=True)
    
    saved_at = Column(DateTime(timezone=True), server_default=func.now())
    
    # Relationships
    # account = relationship('Account')
    # post = relationship('CommunityPost')
    
    __table_args__ = (
        UniqueConstraint('account_id', 'post_id', name='uq_community_saved_item'),
    )


