"""Authenticated APIs for Community, Companion conversations, and role-scoped dashboards.

Community posts are shared experiences, not LCR facts. They never write to care records.
Professional access requires an explicit active access grant with role='professional'.
Team-wide endpoints require an out-of-band staff account flag.
"""
from datetime import datetime, timedelta, timezone
import re
from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.api.authentication import current_account
from app.core.authz import assert_person_access, require_staff
from app.core.database import get_db
from app.models.auth_identity import AccountPersonAccess, AuthAccount
from app.models.care import CareEvent, Caregiver, Person
from app.models.collaboration import (
    CommunityPost,
    CommunityReaction,
    ConversationMessage,
    ConversationSession,
)
from app.models.time_location import Appointment
from app.services.conversation_service import build_companion_reply

router = APIRouter(dependencies=[Depends(current_account)])
community_router = APIRouter(prefix="/community", tags=["community"], dependencies=[Depends(current_account)])
conversation_router = APIRouter(prefix="/conversation", tags=["conversation"], dependencies=[Depends(current_account)])
team_router = APIRouter(prefix="/team", tags=["team"], dependencies=[Depends(require_staff)])
professional_router = APIRouter(prefix="/professional-dashboard", tags=["professional-dashboard"], dependencies=[Depends(current_account)])

ReactionType = Literal["like", "thank_you", "support"]
PostType = Literal["experience", "question"]
Visibility = Literal["public", "anonymous"]


class CommunityPostInput(BaseModel):
    caregiver_id: str = Field(min_length=1, max_length=100)
    content: str = Field(min_length=1, max_length=8000)
    title: Optional[str] = Field(default=None, max_length=180)
    post_type: PostType = "experience"
    visibility: Visibility = "public"
    care_stage: Literal["early", "moderate", "late", "unspecified"] = "unspecified"


class ReactionInput(BaseModel):
    caregiver_id: str = Field(min_length=1, max_length=100)
    reaction_type: ReactionType


class ExperienceSearchInput(BaseModel):
    observation: str = Field(min_length=2, max_length=500)


class ModerationInput(BaseModel):
    status: Literal["approved", "rejected"]


class FlagInput(BaseModel):
    reason: str = Field(default="Reported by a community member", min_length=3, max_length=500)


class ConversationInput(BaseModel):
    person_id: str = Field(min_length=1, max_length=100)
    text: str = Field(min_length=1, max_length=8000)
    session_id: Optional[str] = Field(default=None, max_length=100)


def _tags_for(body: str) -> list[str]:
    allowed = {
        "sundowning", "wandering", "medication", "behavior", "sleep",
        "communication", "mobility", "nutrition", "safety", "activities",
        "routine", "appointments", "hydration", "general",
    }
    found = {tag.lower() for tag in re.findall(r"#([a-zA-Z][a-zA-Z0-9_-]{1,30})", body)}
    tags = sorted(found & allowed)
    return tags[:10] or ["general"]


def _post_output(db: Session, post: CommunityPost, account_id: str) -> dict:
    counts = {kind: 0 for kind in ("like", "thank_you", "support")}
    for kind, count in (
        db.query(CommunityReaction.reaction_type, func.count(CommunityReaction.id))
        .filter(CommunityReaction.post_id == post.id)
        .group_by(CommunityReaction.reaction_type)
        .all()
    ):
        if kind in counts:
            counts[kind] = int(count)
    own = db.query(CommunityReaction).filter(
        CommunityReaction.post_id == post.id,
        CommunityReaction.account_id == account_id,
    ).first()
    return {
        "id": post.id,
        "author_type": "peer_caregiver",
        "author_label": "Anonymous caregiver",
        "author_avatar_seed": "caregiver",
        "title": post.title,
        "body": post.body,
        "media": [],
        "visibility": post.visibility,
        "care_stage": post.care_stage,
        "tags": post.tags or ["general"],
        "created_at": post.created_at.isoformat() if post.created_at else datetime.now(timezone.utc).isoformat(),
        "updated_at": post.updated_at.isoformat() if post.updated_at else None,
        "provenance_source": "user_submitted",
        "reaction_counts": counts,
        "comment_count": 0,
        "user_reaction": own.reaction_type if own else None,
    }


@community_router.get("")
def list_community_posts(
    caregiver_id: Optional[str] = Query(default=None),
    db: Session = Depends(get_db),
    account: AuthAccount = Depends(current_account),
):
    # The existing UI sends the selected care-record ID under the legacy
    # caregiver_id query name. Treat it only as a context authorization check;
    # never infer caregiver identity from it.
    if caregiver_id:
        assert_person_access(db, account.id, caregiver_id, "read")
    posts = (
        db.query(CommunityPost)
        .filter(CommunityPost.status == "published", CommunityPost.visibility.in_([ "public", "anonymous" ]))
        .order_by(CommunityPost.created_at.desc())
        .limit(100)
        .all()
    )
    return {
        "identity": "anonymous_caregiver",
        "posts": [_post_output(db, post, account.id) for post in posts],
    }


@community_router.post("", status_code=201)
def create_community_post(
    payload: CommunityPostInput,
    db: Session = Depends(get_db),
    account: AuthAccount = Depends(current_account),
):
    # This confirms that the author is an authorized caregiver in a care context.
    # The context ID is not stored on the public post and is not an attribution ID.
    assert_person_access(db, account.id, payload.caregiver_id, "write")
    body = payload.content.strip()
    if not body:
        raise HTTPException(status_code=422, detail="Post content cannot be blank")
    post = CommunityPost(
        author_account_id=account.id,
        title=payload.title.strip() if payload.title and payload.title.strip() else None,
        body=body,
        post_type=payload.post_type,
        visibility=payload.visibility,
        care_stage=payload.care_stage,
        tags=_tags_for(body),
        status="published",
    )
    db.add(post)
    db.commit()
    db.refresh(post)
    return {"success": True, "post": _post_output(db, post, account.id)}


@community_router.post("/experience")
def search_shared_experiences(
    payload: ExperienceSearchInput,
    db: Session = Depends(get_db),
    account: AuthAccount = Depends(current_account),
):
    # Literal search over published, user-submitted posts. This is not AI advice
    # and does not claim that another caregiver's experience is medically valid.
    terms = [term.lower() for term in re.findall(r"[a-zA-Z0-9]{3,}", payload.observation)][:8]
    posts = db.query(CommunityPost).filter(CommunityPost.status == "published").order_by(CommunityPost.created_at.desc()).limit(200).all()
    matches = []
    for post in posts:
        haystack = f"{post.title or ''} {post.body} {' '.join(post.tags or [])}".lower()
        matched = [term for term in terms if term in haystack]
        if matched:
            matches.append({
                "post_id": post.id,
                "author_label": "Anonymous caregiver",
                "author_type": "peer_caregiver",
                "content": post.body,
                "tags": post.tags or ["general"],
                "matched_terms": matched,
                "notice": "Peer experience only; not a clinical recommendation.",
            })
        if len(matches) == 10:
            break
    return {"matches": matches, "method": "literal_search", "ai_generated": False}


@community_router.post("/{post_id}/reactions")
def react_to_community_post(
    post_id: str,
    payload: ReactionInput,
    db: Session = Depends(get_db),
    account: AuthAccount = Depends(current_account),
):
    assert_person_access(db, account.id, payload.caregiver_id, "read")
    post = db.query(CommunityPost).filter(
        CommunityPost.id == post_id,
        CommunityPost.status == "published",
    ).first()
    if post is None:
        raise HTTPException(status_code=404, detail="Community post not found")
    existing = db.query(CommunityReaction).filter(
        CommunityReaction.post_id == post_id,
        CommunityReaction.account_id == account.id,
    ).first()
    if existing and existing.reaction_type == payload.reaction_type:
        db.delete(existing)
        result = None
    elif existing:
        existing.reaction_type = payload.reaction_type
        result = payload.reaction_type
    else:
        db.add(CommunityReaction(post_id=post_id, account_id=account.id, reaction_type=payload.reaction_type))
        result = payload.reaction_type
    db.commit()
    return {"success": True, "user_reaction": result, "post_id": post_id}


@community_router.post("/{post_id}/flag")
def flag_community_post(
    post_id: str,
    payload: FlagInput,
    db: Session = Depends(get_db),
    account: AuthAccount = Depends(current_account),
):
    post = db.query(CommunityPost).filter(
        CommunityPost.id == post_id,
        CommunityPost.status == "published",
    ).first()
    if post is None:
        raise HTTPException(status_code=404, detail="Published community post not found")
    if post.author_account_id == account.id:
        raise HTTPException(status_code=403, detail="You cannot report your own post")
    post.status = "flagged"
    post.flagged_by_account_id = account.id
    post.moderation_reason = payload.reason.strip()
    post.updated_at = datetime.now(timezone.utc)
    db.commit()
    return {"success": True, "post_id": post.id, "status": post.status}


@community_router.post("/{post_id}/moderate")
def moderate_community_post(
    post_id: str,
    payload: ModerationInput,
    db: Session = Depends(get_db),
    account: AuthAccount = Depends(require_staff),
):
    post = db.query(CommunityPost).filter(CommunityPost.id == post_id).first()
    if post is None:
        raise HTTPException(status_code=404, detail="Community post not found")
    post.status = "published" if payload.status == "approved" else "rejected"
    post.moderated_by_account_id = account.id
    post.moderated_at = datetime.now(timezone.utc)
    post.updated_at = datetime.now(timezone.utc)
    db.commit()
    return {"success": True, "post_id": post.id, "status": post.status}


@conversation_router.post("/message")
def send_companion_message(
    payload: ConversationInput,
    db: Session = Depends(get_db),
    account: AuthAccount = Depends(current_account),
):
    assert_person_access(db, account.id, payload.person_id, "read")
    session = None
    if payload.session_id:
        session = db.query(ConversationSession).filter(
            ConversationSession.id == payload.session_id,
            ConversationSession.account_id == account.id,
            ConversationSession.person_id == payload.person_id,
            ConversationSession.is_active.is_(True),
        ).first()
        if session is None:
            raise HTTPException(status_code=404, detail="Conversation session not found")
    else:
        session = ConversationSession(account_id=account.id, person_id=payload.person_id, is_active=True)
        db.add(session)
        db.flush()

    user_text = payload.text.strip()
    if not user_text:
        raise HTTPException(status_code=422, detail="Message cannot be blank")
    policy_reply = build_companion_reply(user_text)
    db.add(ConversationMessage(
        session_id=session.id, account_id=account.id, role="user", content=user_text,
        guard_verdict=None, safety_note=None, evidence_ids=[],
    ))
    db.add(ConversationMessage(
        session_id=session.id, account_id=account.id, role="assistant", content=policy_reply.reply,
        guard_verdict=policy_reply.guard_verdict, safety_note=policy_reply.safety_note,
        evidence_ids=policy_reply.evidence_ids,
    ))
    session.updated_at = datetime.now(timezone.utc)
    db.commit()
    return {
        "session_id": session.id,
        "reply": policy_reply.reply,
        "guard_verdict": policy_reply.guard_verdict,
        "safety_note": policy_reply.safety_note,
        "confidence": policy_reply.confidence,
        "evidence_ids": policy_reply.evidence_ids,
        "ai_generated": False,
        "provider_status": "not_configured",
    }


@conversation_router.get("/{session_id}")
def get_conversation_history(
    session_id: str,
    db: Session = Depends(get_db),
    account: AuthAccount = Depends(current_account),
):
    session = db.query(ConversationSession).filter(
        ConversationSession.id == session_id,
        ConversationSession.account_id == account.id,
        ConversationSession.is_active.is_(True),
    ).first()
    if session is None:
        raise HTTPException(status_code=404, detail="Conversation session not found")
    assert_person_access(db, account.id, session.person_id, "read")
    rows = db.query(ConversationMessage).filter(
        ConversationMessage.session_id == session.id,
        ConversationMessage.account_id == account.id,
    ).order_by(ConversationMessage.created_at.asc()).all()
    return {
        "session_id": session.id,
        "person_id": session.person_id,
        "messages": [{
            "id": row.id, "role": row.role, "text": row.content,
            "guard_verdict": row.guard_verdict, "safety_note": row.safety_note,
            "evidence_ids": row.evidence_ids or [],
            "created_at": row.created_at.isoformat() if row.created_at else None,
        } for row in rows],
    }


@team_router.get("/dashboard")
def team_dashboard(db: Session = Depends(get_db)):
    linked_caregivers = db.query(func.count(func.distinct(CommunityPost.author_account_id))).scalar() or 0
    flagged = db.query(CommunityPost).filter(CommunityPost.status == "flagged").order_by(CommunityPost.created_at.desc()).limit(100).all()
    return {
        "caregivers_at_risk": [],
        "posts_to_review": [{
            "id": post.id, "content": post.body, "author_label": "Anonymous caregiver",
            "reason": post.moderation_reason or "flagged_for_review", "tags": post.tags or [],
        } for post in flagged],
        "metrics": {"published_posts": db.query(func.count(CommunityPost.id)).filter(CommunityPost.status == "published").scalar() or 0,
                    "active_conversation_sessions": db.query(func.count(ConversationSession.id)).filter(ConversationSession.is_active.is_(True)).scalar() or 0,
                    "caregiver_accounts_with_posts": linked_caregivers},
        "risk_model_status": "not_configured",
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


@team_router.get("/state-report")
def team_state_report(db: Session = Depends(get_db)):
    tag_counts: dict[str, int] = {}
    posts = db.query(CommunityPost).filter(CommunityPost.status == "published").all()
    for post in posts:
        for tag in set(post.tags or []):
            tag_counts[tag] = tag_counts.get(tag, 0) + 1
    top = sorted(tag_counts.items(), key=lambda item: (-item[1], item[0]))[:10]
    return {
        "total_caregivers": db.query(func.count(func.distinct(CommunityPost.author_account_id))).scalar() or 0,
        "avg_helpfulness": None,
        "top_concerns": [tag for tag, _count in top[:5]],
        "trending_tags": [{"tag": tag, "count": count, "growth": None} for tag, count in top],
        "sample_size": len(posts),
        "helpfulness_status": "not_measured",
        "growth_status": "not_measured",
    }


@router.get("/metrics", dependencies=[Depends(require_staff)])
@team_router.get("/metrics")
def team_metrics(db: Session = Depends(get_db)):
    return {
        "community_posts_published": db.query(func.count(CommunityPost.id)).filter(CommunityPost.status == "published").scalar() or 0,
        "community_posts_flagged": db.query(func.count(CommunityPost.id)).filter(CommunityPost.status == "flagged").scalar() or 0,
        "conversation_sessions": db.query(func.count(ConversationSession.id)).scalar() or 0,
        "conversation_messages": db.query(func.count(ConversationMessage.id)).scalar() or 0,
        "risk_scoring": "not_configured",
    }


@professional_router.get("/")
def professional_dashboard(
    timeframe_days: int = Query(default=7, ge=1, le=90),
    db: Session = Depends(get_db),
    account: AuthAccount = Depends(current_account),
):
    grants = db.query(AccountPersonAccess, Person).join(
        Person, AccountPersonAccess.person_id == Person.id
    ).filter(
        AccountPersonAccess.account_id == account.id,
        AccountPersonAccess.role == "professional",
        AccountPersonAccess.is_active.is_(True),
        AccountPersonAccess.revoked_at.is_(None),
    ).all()
    if not grants:
        raise HTTPException(status_code=403, detail="Verified professional access is required")
    cutoff = datetime.now(timezone.utc) - timedelta(days=timeframe_days)
    patients = []
    alerts = []
    active_today = 0
    for grant, person in grants:
        events = db.query(CareEvent).filter(
            CareEvent.person_id == person.id,
            CareEvent.occurred_at >= cutoff,
        ).order_by(CareEvent.occurred_at.desc()).all()
        caregivers = db.query(Caregiver).filter(Caregiver.person_id == person.id).count()
        upcoming = db.query(Appointment).filter(
            Appointment.person_id == person.id,
            Appointment.status == "scheduled",
            Appointment.scheduled_at >= datetime.now(timezone.utc),
        ).order_by(Appointment.scheduled_at.asc()).first()
        person_alerts = []
        for event in events:
            if event.attention_candidate or event.follow_up_candidate:
                item = {
                    "person_id": person.id,
                    "person_name": person.name,
                    "alert_type": "attention_candidate" if event.attention_candidate else "follow_up_candidate",
                    "title": event.title,
                    "description": event.description or "Recorded event requires review.",
                    "severity": event.significance_verdict or "unclassified",
                    "timestamp": event.occurred_at.isoformat() if event.occurred_at else "",
                    "action_required": bool(event.follow_up_candidate),
                    "suggested_action": None,
                }
                alerts.append(item)
                person_alerts.append(item["title"])
        if any(event.occurred_at and event.occurred_at.date() == datetime.now(timezone.utc).date() for event in events):
            active_today += 1
        latest = events[0].occurred_at.isoformat() if events and events[0].occurred_at else ""
        patients.append({
            "person_id": person.id,
            "person_name": person.name,
            "primary_condition": None,
            "last_updated": latest,
            "alert_count": len(person_alerts),
            "alerts": person_alerts,
            "care_team_size": caregivers,
            "has_recent_updates": bool(events),
            "next_appointment": upcoming.scheduled_at.isoformat() if upcoming else None,
            "compliance_score": None,
        })
    return {
        "total_patients": len(patients),
        "active_patients_today": active_today,
        "patients_with_alerts": sum(1 for patient in patients if patient["alert_count"]),
        "total_alerts": len(alerts),
        "alerts": alerts,
        "patients": patients,
        "professional_name": account.display_name or account.email,
        "professional_role": "professional",
        "specialty": None,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "timeframe_days": timeframe_days,
    }
