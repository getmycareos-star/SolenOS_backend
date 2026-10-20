from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.orm import Session
from typing import List, Optional

from app.core.database import get_db
from app.core.security import (
    get_principal_from_request,
    require_authenticated,
    require_team_access,
    require_care_context_access,
    _log_security_event,
    INPUT_LIMITS,
)
from app.models.community import (
    CommunityPost, CommunityPostStatus, CommunityVisibility,
    CommunityAuthorType, CommunityContentType,
    QuestionStatus, ReactionType, ModerationAction, VerificationStatus
)
from app.models.identity import Account
from app.schemas.community import (
    CommunityPostCreate, CommunityPostUpdate, CommunityPostSummary,
    CommunityReactionCreate, CommunityReaction,
    TeamDashboard, StateReport, ExperienceResult,
    ProfessionalHelpFeed, HelpRequest,
    LCRCommunityReferenceCreate, LCRCommunityReference,
    CommunityReport
)
from app.services.community import CommunityService

router = APIRouter(prefix="/community", tags=["community"])


@router.post("/experience", response_model=ExperienceResult)
def create_experience(
    request: Request,
    observation: str,
    person_id: str,
    db: Session = Depends(get_db),
):
    """Create an experience post and return similar matches.
    Identity (account_id) is derived from the authenticated principal
    never accepted from client input.
    """
    principal = get_principal_from_request(request)
    require_authenticated(principal)
    require_care_context_access(principal, person_id, "experience_post", request)

    if len(observation) > INPUT_LIMITS["community_post"]:
        raise HTTPException(status_code=400,
                            detail=f"Observation text too long: max {INPUT_LIMITS['community_post']} chars")

    service = CommunityService(db)
    post = service.create_post(CommunityPostCreate(
        account_id=principal.account_id,
        person_id=person_id,
        author_type=CommunityAuthorType.PEER_CAREGIVER,
        author_label="Caregiver",
        content_type=CommunityContentType.CAREGIVER_EXPERIENCE,
        title=None,
        body=observation,
        visibility=CommunityVisibility.PUBLIC,
        care_stage=None,
        tags=[],
    ))
    post.status = CommunityPostStatus.APPROVED
    post.moderation_reason = None
    db.commit()
    db.refresh(post)
    return service.match_experience(observation, principal.account_id)


@router.get("", response_model=List[CommunityPostSummary])
def list_community_posts(
    request: Request,
    content_types: Optional[List[CommunityContentType]] = Query(None),
    tags: Optional[List[str]] = Query(None),
    care_stages: Optional[List[str]] = Query(None),
    caregiver_id: Optional[str] = Query(None),
    limit: int = 50,
    db: Session = Depends(get_db),
):
    """List public community posts."""
    principal = get_principal_from_request(request)
    limit = min(limit, INPUT_LIMITS["pagination_max"])
    service = CommunityService(db)
    posts = service.list_public_posts_summary(
        content_types=content_types,
        tags=tags,
        care_stages=care_stages,
        limit=limit,
        account_id=principal.account_id,
        caregiver_id=caregiver_id,
    )
    return posts


@router.post("/{post_id}/reactions", response_model=CommunityReaction)
def create_reaction(
    request: Request,
    post_id: str,
    reaction: CommunityReactionCreate,
    db: Session = Depends(get_db),
):
    """Add a reaction to a post."""
    principal = get_principal_from_request(request)
    if not principal.is_authenticated:
        raise HTTPException(status_code=401, detail="Authentication required to react to posts")

    service = CommunityService(db)
    post = service.get_post(post_id)
    if not post:
        raise HTTPException(status_code=404, detail="Post not found")

    # Override account_id from client → use server-derived principal
    reaction_data = reaction.model_dump()
    reaction_data["account_id"] = principal.account_id
    return service.create_reaction(CommunityReactionCreate(**reaction_data))


@router.post("", response_model=CommunityPostSummary)
def create_community_post(
    request: Request,
    post: CommunityPostCreate,
    db: Session = Depends(get_db),
):
    """Create a new community post (goes to moderation)."""
    principal = require_authenticated(get_principal_from_request(request))

    # Override client-supplied account_id
    post_data = post.model_dump()
    post_data["account_id"] = principal.account_id
    if "person_id" in post_data and post_data["person_id"]:
        require_care_context_access(principal, post_data["person_id"], "community_post", request)

    # If creating an announcement, check permissions and override author info
    if post.content_type == CommunityContentType.ANNouncement:
        if not principal.is_operations:
            raise HTTPException(status_code=403, detail="Only SolenOS Team can create announcements")
        post_data["author_type"] = CommunityAuthorType.SOLANOS_TEAM
        post_data["author_label"] = "SolenOS Team"

    service = CommunityService(db)
    created = service.create_post(CommunityPostCreate(**post_data))
    return service.to_summary(created, principal.account_id)


@router.get("/help-requests", response_model=ProfessionalHelpFeed)
def list_help_requests(
    request: Request,
    limit: int = 50,
    db: Session = Depends(get_db),
):
    """Get unanswered questions for professional feed."""
    principal = get_principal_from_request(request)
    limit = min(limit, INPUT_LIMITS["pagination_max"])
    service = CommunityService(db)
    return service.list_help_requests(limit)


@router.get("/team/dashboard")
def get_team_dashboard(
    request: Request,
    db: Session = Depends(get_db),
):
    """Get team dashboard metrics (operations-only)."""
    principal = require_team_access(get_principal_from_request(request))
    service = CommunityService(db)
    return service.compute_team_dashboard()


@router.get("/team/state-report")
def get_state_report(
    request: Request,
    db: Session = Depends(get_db),
):
    """Get state of dementia care report (operations-only)."""
    principal = require_team_access(get_principal_from_request(request))
    service = CommunityService(db)
    return service.compute_state_report()


@router.post("/{post_id}/moderate", response_model=CommunityPostSummary)
def moderate_post(
    request: Request,
    post_id: str,
    status: CommunityPostStatus,
    moderation_reason: Optional[str] = None,
    care_stage: Optional[str] = None,
    tags: Optional[List[str]] = None,
    db: Session = Depends(get_db),
):
    """Moderate a post (approve, flag, etc.). Requires operations access."""
    principal = require_team_access(get_principal_from_request(request))
    service = CommunityService(db)
    post = service.update_post_moderation(
        post_id,
        status=status,
        moderation_reason=moderation_reason,
        care_stage=care_stage,
        tags=tags,
        moderator_account_id=principal.account_id,
    )
    if not post:
        raise HTTPException(status_code=404, detail="Post not found")
    return service.to_summary(post, principal.account_id)


@router.get("/{post_id}", response_model=CommunityPostSummary)
def get_post(
    request: Request,
    post_id: str,
    db: Session = Depends(get_db),
):
    """Get a single post with reactions."""
    principal = get_principal_from_request(request)
    service = CommunityService(db)
    post = service.get_post(post_id)
    if not post:
        raise HTTPException(status_code=404, detail="Post not found")
    return service.to_summary(post, principal.account_id)


@router.patch("/{post_id}", response_model=CommunityPostSummary)
def update_post(
    request: Request,
    post_id: str,
    update: CommunityPostUpdate,
    db: Session = Depends(get_db),
):
    """Update a post (author or moderator)."""
    principal = require_authenticated(get_principal_from_request(request))
    service = CommunityService(db)
    post = service.get_post(post_id)
    if not post:
        raise HTTPException(status_code=404, detail="Post not found")

    # If updating an announcement, only team members can update
    if post.content_type == CommunityContentType.ANNouncement:
        if not principal.is_operations:
            _log_security_event(
                "authz_failure",
                request=request,
                principal=principal,
                reason="non-team-member attempted to update announcement",
                status_code=403,
            )
            raise HTTPException(status_code=403, detail="Only SolenOS Team can edit announcements")
    else:
        # Only the author or staff can update
        is_author = post.account_id == principal.account_id
        if not is_author and not principal.is_operations:
            _log_security_event(
                "authz_failure",
                request=request,
                principal=principal,
                reason="non-author non-staff attempted post update",
                status_code=403,
            )
            raise HTTPException(status_code=403, detail="Not authorized to edit this post")

    post = service.update_post(post_id, model_dump=update.model_dump(exclude_unset=True), editor_account_id=principal.account_id)
    if not post:
        raise HTTPException(status_code=404, detail="Post not found")
    return service.to_summary(post, principal.account_id)


@router.post("/{post_id}/helpful")
def mark_helpful(
    request: Request,
    post_id: str,
    db: Session = Depends(get_db),
):
    """Mark a post as helpful."""
    principal = require_authenticated(get_principal_from_request(request))
    service = CommunityService(db)
    post = service.mark_post_helpful(post_id)
    if not post:
        raise HTTPException(status_code=404, detail="Post not found")
    return service.to_summary(post, principal.account_id)


@router.post("/seed")
def seed_community(
    request: Request,
    db: Session = Depends(get_db),
):
    """Seed sample community posts (operations-only)."""
    principal = require_team_access(get_principal_from_request(request))
    service = CommunityService(db)
    posts = service.seed_posts(principal.account_id)
    return [service.to_summary(p, principal.account_id) for p in posts]


@router.post("/reference-to-lcr", response_model=LCRCommunityReference)
def create_lcr_reference(
    request: Request,
    reference: LCRCommunityReferenceCreate,
    db: Session = Depends(get_db),
):
    """Create a reference from community content to LCR."""
    principal = require_authenticated(get_principal_from_request(request))
    
    # Verify the user owns the LCR (person_id)
    require_care_context_access(principal, reference.person_id, "create_lcr_reference", request)
    
    # Verify the post exists and is accessible
    service = CommunityService(db)
    post = service.get_post(reference.post_id)
    if not post:
        raise HTTPException(status_code=404, detail="Post not found")
    
    # Check if the post is public or if the user has access to it
    if post.visibility == CommunityVisibility.PRIVATE and post.account_id != principal.account_id:
        raise HTTPException(status_code=403, detail="Not authorized to reference this post")
    
    # Create the reference
    reference_data = reference.model_dump()
    reference_data["account_id"] = principal.account_id
    created_reference = service.create_lcr_reference(LCRCommunityReferenceCreate(**reference_data))
    return created_reference


@router.post("/{post_id}/report", response_model=CommunityReport)
def report_post(
    request: Request,
    post_id: str,
    reason: str,
    description: Optional[str] = None,
    db: Session = Depends(get_db),
):
    """Report a post for moderation."""
    principal = require_authenticated(get_principal_from_request(request))
    
    # Verify the post exists
    service = CommunityService(db)
    post = service.get_post(post_id)
    if not post:
        raise HTTPException(status_code=404, detail="Post not found")
    
    # Create the report
    report = service.create_report(principal.account_id, post_id, reason, description)
    return report


@router.get("/team/reports/{post_id}", response_model=List[CommunityReport])
def get_post_reports(
    request: Request,
    post_id: str,
    db: Session = Depends(get_db),
):
    """Get reports for a post (team-only)."""
    principal = require_team_access(get_principal_from_request(request))
    
    service = CommunityService(db)
    reports = service.get_reports_for_post(post_id)
    return reports


@router.post("/team/reports/{report_id}/status")
def update_report_status(
    request: Request,
    report_id: str,
    status: str,
    action_taken: Optional[str] = None,
    action_taken_by: Optional[str] = None,
    db: Session = Depends(get_db),
):
    """Update the status of a report (team-only)."""
    principal = require_team_access(get_principal_from_request(request))
    
    service = CommunityService(db)
    updated_report = service.update_report_status(report_id, status, action_taken, action_taken_by)
    if not updated_report:
        raise HTTPException(status_code=404, detail="Report not found")
    return updated_report


# --- Team endpoints ---
team_router = APIRouter(prefix="/team", tags=["team"])


@team_router.get("/dashboard")
def get_team_dashboard(
    request: Request,
    db: Session = Depends(get_db),
):
    """Get team dashboard metrics (operations-only)."""
    principal = require_team_access(get_principal_from_request(request))
    service = CommunityService(db)
    return service.compute_team_dashboard()


@team_router.get("/state-report")
def get_state_report(
    request: Request,
    db: Session = Depends(get_db),
):
    """Get state of dementia care report (operations-only)."""
    principal = require_team_access(get_principal_from_request(request))
    service = CommunityService(db)
    return service.compute_state_report()