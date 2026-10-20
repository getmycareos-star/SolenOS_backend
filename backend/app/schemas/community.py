from pydantic import BaseModel, Field, validator
from datetime import datetime
from typing import Optional, List, Any
from enum import Enum
from app.models.community import (
    CommunityContentType, CommunityAuthorType, CommunityPostStatus,
    CommunityVisibility, QuestionStatus, ReactionType, ModerationAction,
    VerificationStatus
)


class MediaAttachment(BaseModel):
    url: str
    mime_type: str
    alt: Optional[str] = None


class CommunityContentType(str, Enum):
    QUESTION = "question"
    DISCUSSION = "discussion"
    CAREGIVER_EXPERIENCE = "caregiver_experience"
    PROFESSIONAL_EDUCATION = "professional_education"
    RESOURCE = "resource"
    ANNOUNCEMENT = "announcement"
    SUPPORT = "support"


class CommunityAuthorType(str, Enum):
    PEER_CAREGIVER = "peer_caregiver"
    VERIFIED_PROFESSIONAL = "verified_professional"
    PROFESSIONAL = "professional"  # Unverified professional
    SOLANOS_TEAM = "solenos_team"


class CommunityPostStatus(str, Enum):
    DRAFT = "draft"
    PENDING_MODERATION = "pending_moderation"
    APPROVED = "approved"
    FLAGGED = "flagged"
    ARCHIVED = "archived"


class CommunityVisibility(str, Enum):
    PUBLIC = "public"
    PRIVATE = "private"
    TEAM_ONLY = "team_only"


class QuestionStatus(str, Enum):
    OPEN = "open"
    RESOLVED = "resolved"
    EXPIRED = "expired"


class ReactionType(str, Enum):
    LOVE = "love"
    SUPPORT = "support"
    APPRECIATE = "appreciate"


class ModerationAction(str, Enum):
    HIDE = "hide"
    REMOVE = "remove"
    RESTORE = "restore"
    LOCK = "lock"
    RESTRICT_USER = "restrict_user"
    SUSPEND_USER = "suspend_user"
    WARN = "warn"
    ESCALATE = "escalate"


class VerificationStatus(str, Enum):
    SUBMITTED = "submitted"
    PENDING = "pending"
    UNDER_REVIEW = "under_review"
    VERIFIED = "verified"
    REJECTED = "rejected"
    REQUIRES_MORE_INFORMATION = "requires_more_information"
    EXPIRING = "expiring"
    REQUIRES_REVERIFICATION = "requires_reverification"
    SUSPENDED = "suspended"
    EXPIRED = "expired"


class LCRReferenceType(str, Enum):
    REFERENCE = "reference"
    CONTEXT = "context"
    QUESTION = "question"
    NOTE = "note"
    RESOURCE = "resource"
    EXPERIENCE = "experience"
    PROFESSIONAL_INFORMATION = "professional_information"


class CommunityPostBase(BaseModel):
    account_id: str
    person_id: str
    author_type: CommunityAuthorType = CommunityAuthorType.PEER_CAREGIVER
    author_label: str
    author_avatar_seed: str
    professional_profile_id: Optional[str] = None
    verification_status: Optional[VerificationStatus] = None
    content_type: CommunityContentType = CommunityContentType.CAREGIVER_EXPERIENCE
    title: Optional[str] = None
    body: str
    media: List[MediaAttachment] = []
    visibility: CommunityVisibility = CommunityVisibility.PUBLIC
    care_stage: Optional[str] = None
    tags: List[str] = []


class CommunityPostCreate(CommunityPostBase):
    pass


class CommunityPostUpdate(BaseModel):
    title: Optional[str] = None
    body: Optional[str] = None
    media: Optional[List[MediaAttachment]] = None
    visibility: Optional[CommunityVisibility] = None
    care_stage: Optional[str] = None
    tags: Optional[List[str]] = None
    status: Optional[CommunityPostStatus] = None


class CommunityPost(CommunityPostBase):
    id: str
    author_avatar_seed: str
    visibility: CommunityVisibility
    care_stage: Optional[str]
    status: CommunityPostStatus
    moderation_reason: Optional[str] = None
    moderation_action_by: Optional[str] = None
    moderation_action_at: Optional[datetime] = None
    question_status: Optional[QuestionStatus] = None
    expires_at: Optional[datetime] = None
    helpful_marks: int = 0
    provenance_source: Optional[str] = None
    provenance_note: Optional[str] = None
    edited_at: Optional[datetime] = None
    edited_by: Optional[str] = None
    created_at: datetime
    updated_at: Optional[datetime] = None

    class Config:
        from_attributes = True


class CommunityPostSummary(BaseModel):
    id: str
    account_id: str
    person_id: str
    author_type: CommunityAuthorType
    author_label: str
    author_avatar_seed: str
    professional_profile_id: Optional[str] = None
    verification_status: Optional[VerificationStatus] = None
    content_type: CommunityContentType
    title: Optional[str] = None
    body: str
    media: List[MediaAttachment] = []
    visibility: CommunityVisibility
    care_stage: Optional[str]
    tags: List[str]
    status: CommunityPostStatus
    moderation_reason: Optional[str] = None
    question_status: Optional[QuestionStatus] = None
    expires_at: Optional[datetime] = None
    helpful_marks: int
    provenance_source: Optional[str] = None
    provenance_note: Optional[str] = None
    edited_at: Optional[datetime] = None
    edited_by: Optional[str] = None
    created_at: datetime
    updated_at: Optional[datetime] = None
    reaction_counts: dict[ReactionType, int]
    user_reaction: Optional[ReactionType] = None

    class Config:
        from_attributes = True


class CommunityReactionBase(BaseModel):
    post_id: str
    account_id: str
    author_type: CommunityAuthorType = CommunityAuthorType.PEER_CAREGIVER
    reaction_type: ReactionType


class CommunityReactionCreate(CommunityReactionBase):
    pass


class CommunityReaction(CommunityReactionBase):
    id: str
    created_at: datetime

    class Config:
        from_attributes = True


class CommunityReport(BaseModel):
    id: str
    reporter_id: str
    post_id: str
    reason: str
    description: Optional[str] = None
    status: str = "submitted"  # submitted, triaged, under_review, actioned, resolved
    action_taken: Optional[str] = None
    action_taken_by: Optional[str] = None
    action_taken_at: Optional[datetime] = None
    created_at: datetime
    updated_at: Optional[datetime] = None

    class Config:
        from_attributes = True


class CommunityModerationAudit(BaseModel):
    id: str
    post_id: str
    moderator_id: str
    action: ModerationAction
    reason: Optional[str] = None
    previous_status: Optional[str] = None
    new_status: Optional[str] = None
    created_at: datetime

    class Config:
        from_attributes = True


class CommunitySavedItem(BaseModel):
    id: str
    account_id: str
    post_id: str
    saved_at: datetime

    class Config:
        from_attributes = True


class LCRCommunityReferenceBase(BaseModel):
    account_id: str
    person_id: str  # The LCR/person this references
    post_id: str
    reference_type: LCRReferenceType
    snapshot_body: Optional[str] = None
    snapshot_created_at: Optional[datetime] = None
    snapshot_reason: Optional[str] = None


class LCRCommunityReferenceCreate(LCRCommunityReferenceBase):
    pass


class LCRCommunityReference(LCRCommunityReferenceBase):
    id: str
    created_at: datetime
    updated_at: Optional[datetime] = None

    class Config:
        from_attributes = True


class ExperienceResult(BaseModel):
    match: dict
    similar: List[dict]


class HelpRequest(BaseModel):
    id: str
    account_id: str
    question: str
    expertise_needed: List[str]
    urgency: str
    status: str
    created_at: datetime


class ProfessionalHelpFeed(BaseModel):
    requests: List[HelpRequest]
    total: int


class CaregiverNeedingAttention(BaseModel):
    account_id: str
    label: str
    risk_level: str  # mild, moderate, high
    distress_signals: int
    last_active: str


class PostNeedsReview(BaseModel):
    id: str
    content: str
    author_label: str
    reason: str
    tags: List[str]


class TrendingTag(BaseModel):
    tag: str
    count: int
    growth: str


class TeamDashboard(BaseModel):
    caregivers_at_risk: List[CaregiverNeedingAttention]
    posts_to_review: List[PostNeedsReview]
    trending_tags: List[TrendingTag]
    total_posts: int
    unanswered_questions: int
    distress_signals_24h: int
    answers_given: int
    professionals_active: int
    questions_answered: int
    avg_helpfulness: float
    expertise_match_rate: int


class StateReport(BaseModel):
    total_caregivers: int
    avg_helpfulness: float
    top_concerns: List[str]
    trending_tags: List[TrendingTag]
    total_posts: int
    total_questions: int
    top_contributors: int