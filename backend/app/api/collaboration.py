import json
import re
from datetime import datetime, timezone
from typing import Optional, List
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
from sqlalchemy import func
from app.core.database import get_db
from app.models.care import Person, Caregiver, CareEvent, Insight
from app.models.time_location import Appointment
from app.models.auth import AuthAccount
from app.models.collaboration import (
    CommunityPost, CommunityReaction, ConversationSession, ConversationMessage,
    ProfessionalAccessGrant,
)

router = APIRouter()


class CommunityPostCreate(BaseModel):
    caregiver_id: str
    author_type: str = "peer_caregiver"
    author_label: str = "Anonymous caregiver"
    post_type: str = "experience"
    content: str = Field(min_length=1, max_length=5000)
    care_stage: Optional[str] = None
    tags: List[str] = Field(default_factory=list)
    visibility: str = "public"


class ReactionCreate(BaseModel):
    caregiver_id: str
    reaction_type: str = Field(min_length=1, max_length=32)


class CompanionMessageCreate(BaseModel):
    person_id: str
    caregiver_id: str
    text: str = Field(min_length=1, max_length=4000)
    session_id: Optional[str] = None


class ProfessionalGrantCreate(BaseModel):
    professional_caregiver_id: str
    person_id: str
    granted_by_caregiver_id: str
    organization: Optional[str] = None
    status: str = "active"


class ModerationUpdate(BaseModel):
    moderation_status: str


def _caregiver_for_person(db: Session, caregiver_id: str, person_id: str) -> Caregiver:
    caregiver = db.query(Caregiver).filter(Caregiver.id == caregiver_id).first()
    person = db.query(Person).filter(Person.id == person_id).first()
    if not person:
        raise HTTPException(status_code=404, detail="Care recipient not found")
    if not caregiver or caregiver.person_id != person_id:
        raise HTTPException(status_code=403, detail="Caregiver is not linked to this care record")
    return caregiver


def _post_payload(db: Session, post: CommunityPost, actor_id: Optional[str] = None):
    reactions = db.query(CommunityReaction).filter(CommunityReaction.post_id == post.id).all()
    counts = {}
    for reaction in reactions:
        counts[reaction.reaction_type] = counts.get(reaction.reaction_type, 0) + 1
    return {
        "id": post.id, "caregiver_id": post.caregiver_id,
        "author_type": post.author_type, "author_label": post.author_label,
        "post_type": post.post_type, "content": post.content,
        "care_stage": post.care_stage, "tags": json.loads(post.tags_json or "[]"),
        "visibility": post.visibility, "moderation_status": post.moderation_status,
        "created_at": post.created_at.isoformat() if post.created_at else None,
        "reaction_counts": counts,
        "user_reaction": next((r.reaction_type for r in reactions if actor_id and r.caregiver_id == actor_id), None),
    }


@router.get("/community")
def list_community_posts(caregiver_id: Optional[str] = None, limit: int = Query(30, ge=1, le=100), db: Session = Depends(get_db)):
    posts = (db.query(CommunityPost)
        .filter(CommunityPost.visibility == "public", CommunityPost.moderation_status == "published")
        .order_by(CommunityPost.created_at.desc()).limit(limit).all())
    return {"posts": [_post_payload(db, post, caregiver_id) for post in posts], "count": len(posts)}


@router.post("/community", status_code=201)
def create_community_post(payload: CommunityPostCreate, db: Session = Depends(get_db)):
    if payload.author_type != "peer_caregiver":
        raise HTTPException(status_code=403, detail="Only caregiver-authored Community posts are accepted; verified roles are assigned by the backend")
    caregiver = db.query(Caregiver).filter(Caregiver.id == payload.caregiver_id).first()
    if not caregiver:
        raise HTTPException(status_code=403, detail="A valid linked caregiver is required")
    if payload.author_type != "peer_caregiver":
        raise HTTPException(status_code=403, detail="Community posts must be authored as a caregiver; verified roles are assigned by SolenOS")
    if payload.visibility not in {"public", "private"}:
        raise HTTPException(status_code=422, detail="visibility must be public or private")
    post = CommunityPost(
        caregiver_id=caregiver.id, author_type="peer_caregiver",
        author_label="Anonymous caregiver",
        post_type=payload.post_type, content=payload.content.strip(),
        care_stage=payload.care_stage, tags_json=json.dumps(payload.tags[:20]),
        visibility=payload.visibility, moderation_status="pending",
    )
    db.add(post); db.commit(); db.refresh(post)
    return _post_payload(db, post, caregiver.id)


@router.post("/community/{post_id}/reactions")
def react_to_community_post(post_id: str, payload: ReactionCreate, db: Session = Depends(get_db)):
    allowed = {"support", "helpful", "relate", "heart"}
    if payload.reaction_type not in allowed:
        raise HTTPException(status_code=422, detail="Unsupported reaction type")
    if not db.query(Caregiver).filter(Caregiver.id == payload.caregiver_id).first():
        raise HTTPException(status_code=403, detail="A valid caregiver is required")
    post = db.query(CommunityPost).filter(CommunityPost.id == post_id, CommunityPost.visibility == "public", CommunityPost.moderation_status == "published").first()
    if not post:
        raise HTTPException(status_code=404, detail="Published community post not found")
    reaction = db.query(CommunityReaction).filter_by(post_id=post_id, caregiver_id=payload.caregiver_id).first()
    if reaction:
        reaction.reaction_type = payload.reaction_type
    else:
        db.add(CommunityReaction(post_id=post_id, caregiver_id=payload.caregiver_id, reaction_type=payload.reaction_type))
    db.commit()
    db.refresh(post)
    return _post_payload(db, post, payload.caregiver_id)


@router.delete("/community/{post_id}/reactions")
def remove_community_reaction(post_id: str, caregiver_id: str, db: Session = Depends(get_db)):
    reaction = db.query(CommunityReaction).filter_by(post_id=post_id, caregiver_id=caregiver_id).first()
    if not reaction:
        return {"ok": True, "removed": False}
    db.delete(reaction); db.commit()
    return {"ok": True, "removed": True}


@router.post("/community/experience")
def match_shared_experiences(payload: dict, db: Session = Depends(get_db)):
    query = str(payload.get("observation", "")).strip()
    if not query:
        raise HTTPException(status_code=422, detail="observation is required")
    terms = {term.lower() for term in re.findall(r"[a-zA-Z0-9']{3,}", query)}
    candidates = (db.query(CommunityPost)
        .filter(CommunityPost.visibility == "public", CommunityPost.moderation_status == "published")
        .order_by(CommunityPost.created_at.desc()).limit(200).all())
    scored = []
    for post in candidates:
        words = {term.lower() for term in re.findall(r"[a-zA-Z0-9']{3,}", post.content + " " + (post.care_stage or "") + " " + (post.tags_json or ""))}
        overlap = terms & words
        if overlap:
            scored.append((len(overlap), post))
    scored.sort(key=lambda pair: pair[0], reverse=True)
    matches = [{"post": _post_payload(db, post), "matched_terms": sorted(terms & {term.lower() for term in re.findall(r"[a-zA-Z0-9']{3,}", post.content + " " + (post.care_stage or "") + " " + (post.tags_json or ""))}), "match_type": "keyword_overlap_not_clinical_advice"} for _, post in scored[:4]]
    return {"matches": matches, "matching_method": "literal keyword overlap; not an AI or clinical assessment"}


@router.get("/team/dashboard")
def team_dashboard(db: Session = Depends(get_db)):
    return {
        "people_count": db.query(func.count(Person.id)).scalar() or 0,
        "caregiver_count": db.query(func.count(Caregiver.id)).scalar() or 0,
        "care_event_count": db.query(func.count(CareEvent.id)).scalar() or 0,
        "appointment_count": db.query(func.count(Appointment.id)).scalar() or 0,
        "community_post_count": db.query(func.count(CommunityPost.id)).scalar() or 0,
        "pending_moderation_count": db.query(func.count(CommunityPost.id)).filter(CommunityPost.moderation_status == "pending").scalar() or 0,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "data_source": "database aggregates",
    }


@router.get("/team/state-report")
def team_state_report(db: Session = Depends(get_db)):
    event_rows = db.query(CareEvent.event_type, func.count(CareEvent.id)).group_by(CareEvent.event_type).all()
    appointment_rows = db.query(Appointment.status, func.count(Appointment.id)).group_by(Appointment.status).all()
    post_rows = db.query(CommunityPost.moderation_status, func.count(CommunityPost.id)).group_by(CommunityPost.moderation_status).all()
    return {
        "care_events_by_type": [{"event_type": key, "count": count} for key, count in event_rows],
        "appointments_by_status": [{"status": key, "count": count} for key, count in appointment_rows],
        "community_posts_by_moderation_status": [{"status": key, "count": count} for key, count in post_rows],
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "limitations": ["Aggregates describe records stored in this database; they are not population-level dementia-care statistics."],
    }


@router.get("/metrics")
def service_metrics(db: Session = Depends(get_db)):
    return {
        "database": "reachable",
        "counts": {
            "persons": db.query(func.count(Person.id)).scalar() or 0,
            "caregivers": db.query(func.count(Caregiver.id)).scalar() or 0,
            "care_events": db.query(func.count(CareEvent.id)).scalar() or 0,
            "appointments": db.query(func.count(Appointment.id)).scalar() or 0,
            "community_posts": db.query(func.count(CommunityPost.id)).scalar() or 0,
            "conversation_messages": db.query(func.count(ConversationMessage.id)).scalar() or 0,
        },
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


@router.get("/team/moderation")
def list_moderation_queue(status: str = "pending", limit: int = Query(50, ge=1, le=200), db: Session = Depends(get_db)):
    posts = db.query(CommunityPost).filter(CommunityPost.moderation_status == status).order_by(CommunityPost.created_at.asc()).limit(limit).all()
    return {"posts": [_post_payload(db, post) for post in posts], "count": len(posts)}


@router.patch("/team/moderation/{post_id}")
def moderate_community_post(post_id: str, payload: ModerationUpdate, db: Session = Depends(get_db)):
    if payload.moderation_status not in {"published", "hidden", "removed", "pending"}:
        raise HTTPException(status_code=422, detail="Unsupported moderation status")
    post = db.query(CommunityPost).filter(CommunityPost.id == post_id).first()
    if not post:
        raise HTTPException(status_code=404, detail="Community post not found")
    post.moderation_status = payload.moderation_status
    db.commit(); db.refresh(post)
    return {"id": post.id, "moderation_status": post.moderation_status}


@router.post("/conversation/message")
def send_companion_message(payload: CompanionMessageCreate, db: Session = Depends(get_db)):
    caregiver = _caregiver_for_person(db, payload.caregiver_id, payload.person_id)
    if payload.session_id:
        session = db.query(ConversationSession).filter_by(id=payload.session_id, person_id=payload.person_id, caregiver_id=caregiver.id, status="active").first()
        if not session:
            raise HTTPException(status_code=404, detail="Active conversation not found for this caregiver and care recipient")
    else:
        session = ConversationSession(person_id=payload.person_id, caregiver_id=caregiver.id, title=payload.text[:80])
        db.add(session); db.commit(); db.refresh(session)
    user_message = ConversationMessage(session_id=session.id, role="user", content=payload.text.strip())
    db.add(user_message); db.commit(); db.refresh(user_message)
    lowered = payload.text.lower()
    safety_terms = ("can't breathe", "cannot breathe", "not breathing", "unconscious", "stroke", "severe bleeding", "suicide", "trying to kill")
    if any(term in lowered for term in safety_terms):
        reply = "This may be an emergency. Contact local emergency services or seek urgent medical help now. I cannot assess or diagnose an emergency through chat."
        verdict, safety_note, evidence_ids = "blocked", "Possible emergency language detected; urgent human assessment is needed.", []
    else:
        events = db.query(CareEvent).filter(CareEvent.person_id == payload.person_id).order_by(CareEvent.occurred_at.desc()).limit(5).all()
        appointments = db.query(Appointment).filter(Appointment.person_id == payload.person_id).order_by(Appointment.scheduled_at.asc()).limit(5).all()
        insights = db.query(Insight).filter(Insight.person_id == payload.person_id, Insight.is_active.is_(True)).order_by(Insight.created_at.desc()).limit(3).all()
        lines = ["I can help you review what SolenOS has recorded. I do not diagnose or recommend medication changes."]
        if events:
            lines.append("Recent recorded care events:")
            lines.extend([f"- {event.title}: {event.description or 'No additional detail recorded.'} (event ID: {event.id})" for event in events])
        else:
            lines.append("There are no care events recorded for this person in the available record.")
        if appointments:
            lines.append("Appointments currently recorded:")
            lines.extend([f"- {appt.title} at {appt.scheduled_at.isoformat()} (status: {appt.status}; appointment ID: {appt.id})" for appt in appointments])
        if insights:
            lines.append("Existing system insights (not diagnoses):")
            lines.extend([f"- {insight.title}: {insight.description} (insight ID: {insight.id}; confidence field: {insight.confidence})" for insight in insights])
        lines.append("Your message has been saved to this conversation. If you need a decision, I can help identify which recorded facts are relevant and what remains unclear.")
        reply = "\n".join(lines)
        verdict, safety_note, evidence_ids = "record_review", None, [event.id for event in events] + [insight.id for insight in insights]
    assistant_message = ConversationMessage(session_id=session.id, role="assistant", content=reply, guard_verdict=verdict, safety_note=safety_note, evidence_ids_json=json.dumps(evidence_ids))
    db.add(assistant_message); db.commit(); db.refresh(assistant_message)
    return {
        "session_id": session.id, "person_id": payload.person_id,
        "reply": reply, "guard_verdict": verdict, "safety_note": safety_note,
        "confidence": None, "evidence_ids": evidence_ids,
        "messages": [
            {"id": user_message.id, "role": "user", "text": user_message.content, "created_at": user_message.created_at.isoformat() if user_message.created_at else None},
            {"id": assistant_message.id, "role": "assistant", "text": assistant_message.content, "created_at": assistant_message.created_at.isoformat() if assistant_message.created_at else None},
        ],
    }


@router.get("/conversation/{person_id}")
def list_conversations(person_id: str, caregiver_id: str, db: Session = Depends(get_db)):
    _caregiver_for_person(db, caregiver_id, person_id)
    sessions = db.query(ConversationSession).filter_by(person_id=person_id, caregiver_id=caregiver_id).order_by(ConversationSession.created_at.desc()).all()
    return {"sessions": [{"id": s.id, "title": s.title, "status": s.status, "created_at": s.created_at.isoformat() if s.created_at else None} for s in sessions]}


@router.get("/conversation/{session_id}/messages")
def list_conversation_messages(session_id: str, caregiver_id: str, db: Session = Depends(get_db)):
    session = db.query(ConversationSession).filter_by(id=session_id, caregiver_id=caregiver_id).first()
    if not session:
        raise HTTPException(status_code=404, detail="Conversation not found")
    rows = db.query(ConversationMessage).filter_by(session_id=session_id).order_by(ConversationMessage.created_at.asc()).all()
    return {"session_id": session.id, "messages": [{"id": m.id, "role": m.role, "text": m.content, "guard_verdict": m.guard_verdict, "safety_note": m.safety_note, "evidence_ids": json.loads(m.evidence_ids_json or "[]"), "created_at": m.created_at.isoformat() if m.created_at else None} for m in rows]}


@router.post("/professional/access-grants")
def create_professional_access_grant(payload: ProfessionalGrantCreate, db: Session = Depends(get_db)):
    _caregiver_for_person(db, payload.granted_by_caregiver_id, payload.person_id)
    grantor_account = db.query(AuthAccount).filter_by(
        caregiver_id=payload.granted_by_caregiver_id, is_active=True
    ).first()
    if not grantor_account or grantor_account.role != "caregiver":
        raise HTTPException(status_code=403, detail="Only an active family caregiver account can grant professional access")
    professional = db.query(Caregiver).filter_by(id=payload.professional_caregiver_id).first()
    if not professional:
        raise HTTPException(status_code=404, detail="Professional account record not found")
    professional_account = db.query(AuthAccount).filter_by(
        caregiver_id=payload.professional_caregiver_id, is_active=True
    ).first()
    if not professional_account or professional_account.role != "professional":
        raise HTTPException(status_code=403, detail="Professional access can only be granted to an active, verified professional account")
    if payload.status not in {"active", "revoked"}:
        raise HTTPException(status_code=422, detail="Grant status must be active or revoked")
    existing = db.query(ProfessionalAccessGrant).filter_by(professional_caregiver_id=payload.professional_caregiver_id, person_id=payload.person_id).first()
    if existing:
        existing.status = payload.status
        existing.organization = payload.organization
        existing.granted_by_caregiver_id = payload.granted_by_caregiver_id
        grant = existing
    else:
        grant = ProfessionalAccessGrant(professional_caregiver_id=payload.professional_caregiver_id, person_id=payload.person_id, status=payload.status, organization=payload.organization, granted_by_caregiver_id=payload.granted_by_caregiver_id)
        db.add(grant)
    db.commit(); db.refresh(grant)
    return {"id": grant.id, "professional_caregiver_id": grant.professional_caregiver_id, "person_id": grant.person_id, "status": grant.status, "organization": grant.organization}


@router.get("/professional/dashboard")
def professional_dashboard(professional_caregiver_id: str, db: Session = Depends(get_db)):
    grants = db.query(ProfessionalAccessGrant).filter_by(professional_caregiver_id=professional_caregiver_id, status="active").all()
    result = []
    for grant in grants:
        person = db.query(Person).filter_by(id=grant.person_id).first()
        if not person:
            continue
        result.append({
            "person": {"id": person.id, "name": person.name},
            "access_grant_id": grant.id,
            "care_events_count": db.query(func.count(CareEvent.id)).filter_by(person_id=person.id).scalar() or 0,
            "appointments_count": db.query(func.count(Appointment.id)).filter_by(person_id=person.id).scalar() or 0,
            "active_insights_count": db.query(func.count(Insight.id)).filter_by(person_id=person.id, is_active=True).scalar() or 0,
            "last_events": [{"id": e.id, "title": e.title, "description": e.description, "occurred_at": e.occurred_at.isoformat() if e.occurred_at else None} for e in db.query(CareEvent).filter_by(person_id=person.id).order_by(CareEvent.occurred_at.desc()).limit(5).all()],
        })
    return {"professional_caregiver_id": professional_caregiver_id, "care_recipients": result, "count": len(result), "access_control": "active grant records; caller authentication must still be enforced by auth middleware"}
