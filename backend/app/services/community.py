from sqlalchemy.orm import Session
from datetime import datetime, timedelta, timezone
from typing import List, Optional, Dict, Any
from uuid import uuid4
from app.models.community import (
    CommunityPost, CommunityReaction, CommunityPostStatus,
    CommunityVisibility, CommunityAuthorType, CommunityContentType,
    QuestionStatus, ReactionType, ModerationAction, VerificationStatus,
    CommunityReport, CommunityModerationAudit, CommunitySavedItem,
    lcr_community_reference
)
from app.models.caregiver_intelligence import CaregiverProfile
from app.models.identity import Account, Person, ProfessionalProfile
from app.schemas.community import (
    CommunityPostCreate, CommunityPostUpdate, CommunityPostSummary,
    CommunityReactionCreate, CommunityReaction,
    TeamDashboard, StateReport, ExperienceResult,
    CaregiverNeedingAttention, PostNeedsReview, TrendingTag,
    ProfessionalHelpFeed, HelpRequest,
    LCRCommunityReferenceCreate, LCRCommunityReference
)


class CommunityService:
    def __init__(self, db: Session):
        self.db = db

    def _avatar_seed(self, label: str) -> str:
        return "".join(w[0] for w in label.split() if w).lower()[:2]

    def _create_post_id(self) -> str:
        return f"cp_{int(datetime.now().timestamp() * 1000)}_{uuid4().hex[:7]}"

    def _create_reaction_id(self) -> str:
        return f"cr_{int(datetime.now().timestamp() * 1000)}_{uuid4().hex[:7]}"

    def _create_report_id(self) -> str:
        return f"crpt_{int(datetime.now().timestamp() * 1000)}_{uuid4().hex[:7]}"

    def _create_moderation_audit_id(self) -> str:
        return f"cmaud_{int(datetime.now().timestamp() * 1000)}_{uuid4().hex[:7]}"

    def _create_saved_item_id(self) -> str:
        return f"csit_{int(datetime.now().timestamp() * 1000)}_{uuid4().hex[:7]}"

    def _create_lcr_reference_id(self) -> str:
        return f"lcrref_{int(datetime.now().timestamp() * 1000)}_{uuid4().hex[:7]}"

    def _expiry_for(self, title: Optional[str], body: str) -> Optional[datetime]:
        if title is None or body.strip() == "":
            return datetime.now(timezone.utc)
        return datetime.now(timezone.utc) + timedelta(hours=DEFAULT_RELEVANCE_TTL_HOURS)

    # --- Post CRUD ---
    def create_post(self, input: CommunityPostCreate) -> CommunityPost:
        # Determine if the post is an announcement and set visibility accordingly
        visibility = input.visibility
        if input.content_type == CommunityContentType.ANNouncement:
            visibility = CommunityVisibility.PUBLIC  # Announcements are public by default

        post = CommunityPost(
            id=self._create_post_id(),
            account_id=input.account_id,
            person_id=input.person_id,
            author_type=input.author_type,
            author_label=input.author_label,
            author_avatar_seed=self._avatar_seed(input.author_label),
            professional_profile_id=input.professional_profile_id,
            verification_status=input.verification_status,
            content_type=input.content_type,
            title=input.title,
            body=input.body.strip(),
            media=[m.model_dump() for m in input.media] if input.media else [],
            visibility=visibility,
            care_stage=input.care_stage,
            tags=input.tags if input.tags else [],
            status=CommunityPostStatus.PENDING_MODERATION,
            moderation_reason="pending_review",
            question_status=QuestionStatus.OPEN if input.content_type == CommunityContentType.QUESTION else None,
            expires_at=None if input.content_type == CommunityContentType.ANNouncement else self._expiry_for(input.title, input.body),
            helpful_marks=0,
            provenance_source=f"community:{input.author_type}",
        )
        self.db.add(post)
        self.db.commit()
        self.db.refresh(post)
        return post

    def get_post(self, post_id: str) -> Optional[CommunityPost]:
        return self.db.query(CommunityPost).filter(CommunityPost.id == post_id).first()

    def list_posts_for_account(self, account_id: str) -> List[CommunityPost]:
        return (
            self.db.query(CommunityPost)
            .filter(CommunityPost.account_id == account_id)
            .order_by(CommunityPost.created_at.desc())
            .all()
        )

    def list_public_posts(
        self,
        content_types: Optional[List[CommunityContentType]] = None,
        tags: Optional[List[str]] = None,
        care_stages: Optional[List[str]] = None,
        limit: int = 50,
    ) -> List[CommunityPost]:
        query = (
            self.db.query(CommunityPost)
            .filter(
                CommunityPost.visibility == CommunityVisibility.PUBLIC,
                CommunityPost.status == CommunityPostStatus.APPROVED,
            )
            .order_by(
                # Announcements first
                CommunityPost.content_type != CommunityContentType.ANNouncement,
                CommunityPost.created_at.desc()
            )
        )
        if content_types:
            query = query.filter(CommunityPost.content_type.in_(content_types))
        if tags:
            for tag in tags:
                query = query.filter(CommunityPost.tags.contains([tag]))
        if care_stages:
            query = query.filter(CommunityPost.care_stage.in_(care_stages))
        return query.limit(limit).all()

    def list_pending_posts(self) -> List[CommunityPost]:
        return (
            self.db.query(CommunityPost)
            .filter(
                CommunityPost.status.in_([
                    CommunityPostStatus.PENDING_MODERATION,
                    CommunityPostStatus.FLAGGED,
                ])
            )
            .order_by(CommunityPost.created_at.asc())
            .all()
        )

    def list_unanswered_questions(self, limit: int = 20) -> List[CommunityPost]:
        return (
            self.db.query(CommunityPost)
            .filter(
                CommunityPost.content_type == CommunityContentType.QUESTION,
                CommunityPost.question_status == QuestionStatus.OPEN,
                CommunityPost.status == CommunityPostStatus.APPROVED,
            )
            .order_by(CommunityPost.created_at.asc())
            .limit(limit)
            .all()
        )

    def count_unanswered_questions(self) -> int:
        return (
            self.db.query(CommunityPost)
            .filter(
                CommunityPost.content_type == CommunityContentType.QUESTION,
                CommunityPost.question_status == QuestionStatus.OPEN,
            )
            .count()
        )

    def update_post_moderation(
        self,
        post_id: str,
        status: Optional[CommunityPostStatus] = None,
        moderation_reason: Optional[str] = None,
        care_stage: Optional[str] = None,
        tags: Optional[List[str]] = None,
        moderator_account_id: Optional[str] = None,
    ) -> Optional[CommunityPost]:
        post = self.get_post(post_id)
        if not post:
            return None
        if status is not None:
            post.status = status
        if moderation_reason is not None:
            post.moderation_reason = moderation_reason
        if care_stage is not None:
            post.care_stage = care_stage
        if tags is not None:
            post.tags = tags
        if moderator_account_id is not None:
            post.moderation_action_by = moderator_account_id
            post.moderation_action_at = datetime.now(timezone.utc)
        post.updated_at = datetime.now(timezone.utc)
        if status:
            post.provenance_source = f"community:moderated({status.value})"
        self.db.commit()
        self.db.refresh(post)
        return post

    def update_post(
        self,
        post_id: str,
        patch: dict,
        editor_account_id: Optional[str] = None,
    ) -> Optional[CommunityPost]:
        post = self.get_post(post_id)
        if not post:
            return None
        for k, v in patch.items():
            if hasattr(post, k):
                setattr(post, k, v)
        if editor_account_id is not None:
            post.edited_by = editor_account_id
            post.edited_at = datetime.now(timezone.utc)
        post.updated_at = datetime.now(timezone.utc)
        self.db.commit()
        self.db.refresh(post)
        return post

    def mark_post_helpful(self, post_id: str) -> Optional[CommunityPost]:
        post = self.get_post(post_id)
        if not post:
            return None
        post.helpful_marks += 1
        self.db.commit()
        self.db.refresh(post)
        return post

    def archive_expired_posts(self) -> int:
        now = datetime.now(timezone.utc)
        posts = (
            self.db.query(CommunityPost)
            .filter(
                CommunityPost.expires_at != None,
                CommunityPost.expires_at <= now,
                CommunityPost.status != CommunityPostStatus.ARCHIVED,
            )
            .all()
        )
        for post in posts:
            post.status = CommunityPostStatus.ARCHIVED
            post.updated_at = now
            post.provenance_source = "community:auto-archive"
        self.db.commit()
        return len(posts)

    def seed_posts(self, account_id: str) -> List[CommunityPost]:
        # Get the account to verify it exists and get team access info
        account = self.db.query(Account).filter(Account.id == account_id).first()
        if not account:
            return []
         
        # Only create seed posts if the account has team access (SolenOS Team)
        # In a real implementation, we'd check team permissions more thoroughly
        # For now, we'll create a welcome announcement from SolenOS Team
         
        # Create a SolenOS Team announcement
        announcement_post = CommunityPost(
            id=self._create_post_id(),
            account_id=account_id,
            person_id=account_id,  # Using account_id as person_id for simplicity in seed
            author_type=CommunityAuthorType.SOLANOS_TEAM,
            author_label="SolenOS Team",
            author_avatar_seed=self._avatar_seed("SolenOS Team"),
            professional_profile_id=None,
            verification_status=None,
            content_type=CommunityContentType.ANNouncement,
            title="Welcome to SolenOS Community",
            body="Welcome to the SolenOS Community! This is a space for caregivers to share experiences, ask questions, and find support. Remember that shared experiences are not medical advice - always consult with your clinician for care decisions.",
            media=[],
            visibility=CommunityVisibility.PUBLIC,
            care_stage=None,
            tags=["announcement", "welcome"],
            status=CommunityPostStatus.APPROVED,
            moderation_reason=None,
            question_status=None,
            expires_at=None,
            helpful_marks=0,
            provenance_source="community:solenos_team",
        )
        self.db.add(announcement_post)
        self.db.commit()
        self.db.refresh(announcement_post)
         
        return [announcement_post]

    # --- Reactions ---
    def create_reaction(self, input: CommunityReactionCreate) -> CommunityReaction:
        reaction = CommunityReaction(
            id=self._create_reaction_id(),
            post_id=input.post_id,
            account_id=input.account_id,
            author_type=input.author_type,
            reaction_type=input.reaction_type,
        )
        self.db.add(reaction)
        self.db.commit()
        self.db.refresh(reaction)
        return reaction

    def get_reactions_for_post(self, post_id: str) -> List[CommunityReaction]:
        return (
            self.db.query(CommunityReaction)
            .filter(CommunityReaction.post_id == post_id)
            .order_by(CommunityReaction.created_at.asc())
            .all()
        )

    def get_reaction_counts(self, post_id: str) -> Dict[ReactionType, int]:
        counts = {r: 0 for r in ReactionType}
        for reaction in self.get_reactions_for_post(post_id):
            counts[reaction.reaction_type] += 1
        return counts

    def get_user_reaction(self, post_id: str, account_id: str) -> Optional[ReactionType]:
        for reaction in self.get_reactions_for_post(post_id):
            if reaction.account_id == account_id:
                return reaction.reaction_type
        return None

    # --- LCR References ---
    def create_lcr_reference(self, input: LCRCommunityReferenceCreate) -> CommunitySavedItem:
        # First, create the saved item (which is the reference)
        saved_item = CommunitySavedItem(
            id=self._create_saved_item_id(),
            account_id=input.account_id,
            post_id=input.post_id,
            saved_at=datetime.now(timezone.utc),
        )
        self.db.add(saved_item)
        self.db.flush()  # To get the ID for the reference

        # Create the LCR-Community reference
        lcr_ref = lcr_community_reference.insert().values(
            id=self._create_lcr_reference_id(),
            lcr_id=input.person_id,  # Assuming the LCR is identified by person_id
            community_post_id=input.post_id,
            saved_by_user_id=input.account_id,
            reference_type=input.reference_type,
            snapshot_body=input.snapshot_body,
            snapshot_created_at=input.snapshot_created_at,
            snapshot_reason=input.snapshot_reason,
        )
        self.db.execute(lcr_ref)
        self.db.commit()
        self.db.refresh(saved_item)
        return saved_item

    def get_lcr_references_for_post(self, post_id: str) -> List[Dict]:
        # Returns a list of dictionaries with reference details
        query = self.db.query(lcr_community_reference).filter(lcr_community_reference.c.community_post_id == post_id)
        results = []
        for row in self.db.execute(query):
            results.append({
                "id": row.id,
                "lcr_id": row.lcr_id,
                "community_post_id": row.community_post_id,
                "saved_by_user_id": row.saved_by_user_id,
                "reference_type": row.reference_type,
                "created_at": row.created_at,
                "updated_at": row.updated_at,
                "snapshot_body": row.snapshot_body,
                "snapshot_created_at": row.snapshot_created_at,
                "snapshot_reason": row.snapshot_reason,
            })
        return results

    def get_lcr_references_for_person(self, person_id: str) -> List[Dict]:
        query = self.db.query(lcr_community_reference).filter(lcr_community_reference.c.lcr_id == person_id)
        results = []
        for row in self.db.execute(query):
            results.append({
                "id": row.id,
                "lcr_id": row.lcr_id,
                "community_post_id": row.community_post_id,
                "saved_by_user_id": row.saved_by_user_id,
                "reference_type": row.reference_type,
                "created_at": row.created_at,
                "updated_at": row.updated_at,
                "snapshot_body": row.snapshot_body,
                "snapshot_created_at": row.snapshot_created_at,
                "snapshot_reason": row.snapshot_reason,
            })
        return results

    # --- Reports ---
    def create_report(self, reporter_account_id: str, post_id: str, reason: str, description: Optional[str] = None) -> CommunityReport:
        report = CommunityReport(
            id=self._create_report_id(),
            reporter_id=reporter_account_id,
            post_id=post_id,
            reason=reason,
            description=description,
            status="submitted",
        )
        self.db.add(report)
        self.db.commit()
        self.db.refresh(report)
        return report

    def get_reports_for_post(self, post_id: str) -> List[CommunityReport]:
        return (
            self.db.query(CommunityReport)
            .filter(CommunityReport.post_id == post_id)
            .order_by(CommunityReport.created_at.desc())
            .all()
        )

    def update_report_status(
        self,
        report_id: str,
        status: str,
        action_taken: Optional[str] = None,
        action_taken_by: Optional[str] = None,
    ) -> Optional[CommunityReport]:
        report = self.db.query(CommunityReport).filter(CommunityReport.id == report_id).first()
        if not report:
            return None
        report.status = status
        if action_taken is not None:
            report.action_taken = action_taken
        if action_taken_by is not None:
            report.action_taken_by = action_taken_by
            report.action_taken_at = datetime.now(timezone.utc)
        self.db.commit()
        self.db.refresh(report)
        return report

    # --- Moderation Audit ---
    def create_moderation_audit(
        self,
        post_id: str,
        moderator_account_id: str,
        action: ModerationAction,
        reason: Optional[str] = None,
        previous_status: Optional[str] = None,
        new_status: Optional[str] = None,
    ) -> CommunityModerationAudit:
        audit = CommunityModerationAudit(
            id=self._create_moderation_audit_id(),
            post_id=post_id,
            moderator_id=moderator_account_id,
            action=action,
            reason=reason,
            previous_status=previous_status,
            new_status=new_status,
        )
        # Compute and set the content hash
        audit.content_hash = audit.compute_content_hash()
        # Set the previous hash for chaining
        audit.previous_hash = CommunityModerationAudit.get_latest_hash(self.db)
        
        self.db.add(audit)
        self.db.commit()
        self.db.refresh(audit)
        return audit

    def get_moderation_audit_for_post(self, post_id: str) -> List[CommunityModerationAudit]:
        return (
            self.db.query(CommunityModerationAudit)
            .filter(CommunityModerationAudit.post_id == post_id)
            .order_by(CommunityModerationAudit.created_at.desc())
            .all()
        )

    # --- Saved Items ---
    def get_saved_items_for_account(self, account_id: str) -> List[CommunitySavedItem]:
        return (
            self.db.query(CommunitySavedItem)
            .filter(CommunitySavedItem.account_id == account_id)
            .order_by(CommunitySavedItem.saved_at.desc())
            .all()
        )

    def is_post_saved_by_account(self, post_id: str, account_id: str) -> bool:
        return (
            self.db.query(CommunitySavedItem)
            .filter(
                CommunitySavedItem.post_id == post_id,
                 CommunitySavedItem.account_id == account_id,
            )
            .first()
            is not None
        )

    # --- Helper methods for API ---
    def to_summary(
        self,
        post: CommunityPost,
        account_id: Optional[str] = None,
    ) -> CommunityPostSummary:
        counts = self.get_reaction_counts(post.id)
        user_reaction = self.get_user_reaction(post.id, account_id) if account_id else None
        return CommunityPostSummary(
            id=post.id,
            account_id=post.account_id,
            person_id=post.person_id,
            author_type=post.author_type,
            author_label=post.author_label,
            author_avatar_seed=post.author_avatar_seed,
            professional_profile_id=post.professional_profile_id,
            verification_status=post.verification_status,
            content_type=post.content_type,
            title=post.title,
            body=post.body,
            media=post.media,
            visibility=post.visibility,
            care_stage=post.care_stage,
            tags=post.tags or [],
            status=post.status,
            moderation_reason=post.moderation_reason,
            question_status=post.question_status,
            expires_at=post.expires_at,
            helpful_marks=post.helpful_marks,
            provenance_source=post.provenance_source,
            provenance_note=post.provenance_note,
            edited_at=post.edited_at,
            edited_by=post.edited_by,
            created_at=post.created_at,
            updated_at=post.updated_at,
            reaction_counts=counts,
            user_reaction=user_reaction,
        )

    def list_posts_for_account_summary(self, account_id: str) -> List[CommunityPostSummary]:
        posts = self.list_posts_for_account(account_id)
        return [self.to_summary(p, account_id) for p in posts]

    def list_public_posts_summary(
        self,
        content_types: Optional[List[CommunityContentType]] = None,
        tags: Optional[List[str]] = None,
        care_stages: Optional[List[str]] = None,
        limit: int = 50,
        account_id: Optional[str] = None,
        caregiver_id: Optional[str] = None,
    ) -> List[CommunityPostSummary]:
        posts = self.list_public_posts(content_types, tags, care_stages, limit)
        if caregiver_id is not None:
            posts = [p for p in posts if p.account_id == caregiver_id]
        return [self.to_summary(p, account_id) for p in posts]