import json
from datetime import datetime, timezone
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.models.care import CareEvent, Caregiver, Person
from app.models.collaboration import CommunityPost
from app.models.time_location import Appointment

router = APIRouter()


class ModerationUpdate(BaseModel):
    moderation_status: Literal["published", "hidden", "removed"]


def _generated_at() -> str:
    return datetime.now(timezone.utc).isoformat()


@router.get("/team/dashboard")
def team_dashboard(db: Session = Depends(get_db)):
    """Database-backed aggregate counts; never substitute demo metrics."""
    total_people = db.query(func.count(Person.id)).scalar()
    total_caregivers = db.query(func.count(Caregiver.id)).scalar()
    total_events = db.query(func.count(CareEvent.id)).scalar()
    total_appointments = db.query(func.count(Appointment.id)).scalar()
    total_posts = db.query(func.count(CommunityPost.id)).scalar()
    pending_posts = db.query(func.count(CommunityPost.id)).filter(
        CommunityPost.moderation_status == "pending"
    ).scalar()
    return {
        "people_count": int(total_people or 0),
        "caregiver_count": int(total_caregivers or 0),
        "care_event_count": int(total_events or 0),
        "appointment_count": int(total_appointments or 0),
        "community_post_count": int(total_posts or 0),
        "pending_moderation_count": int(pending_posts or 0),
        "generated_at": _generated_at(),
        "data_source": "database",
    }


@router.get("/team/state-report")
def team_state_report(db: Session = Depends(get_db)):
    events = (
        db.query(CareEvent.event_type, func.count(CareEvent.id))
        .group_by(CareEvent.event_type)
        .order_by(CareEvent.event_type.asc())
        .all()
    )
    appointments = (
        db.query(Appointment.status, func.count(Appointment.id))
        .group_by(Appointment.status)
        .order_by(Appointment.status.asc())
        .all()
    )
    posts = (
        db.query(CommunityPost.moderation_status, func.count(CommunityPost.id))
        .group_by(CommunityPost.moderation_status)
        .order_by(CommunityPost.moderation_status.asc())
        .all()
    )
    return {
        "care_events_by_type": [{"event_type": key, "count": count} for key, count in events],
        "appointments_by_status": [{"status": key, "count": count} for key, count in appointments],
        "community_posts_by_moderation_status": [
            {"status": key, "count": count} for key, count in posts
        ],
        "generated_at": _generated_at(),
        "limitations": [
            "Counts describe rows currently stored in SolenOS and are not population-level estimates.",
            "A zero count means no matching rows were returned by the database query.",
        ],
    }


@router.get("/team/moderation")
def team_moderation_queue(
    status: str = Query(default="pending", pattern="^(pending|published|hidden|removed)$"),
    db: Session = Depends(get_db),
):
    rows = (
        db.query(CommunityPost)
        .filter(CommunityPost.moderation_status == status)
        .order_by(CommunityPost.created_at.asc(), CommunityPost.id.asc())
        .all()
    )
    posts = []
    for post in rows:
        try:
            tags = json.loads(post.tags_json)
        except (TypeError, ValueError) as exc:
            raise HTTPException(status_code=500, detail="A stored Community post has invalid tags; queue data is unavailable.") from exc
        if not isinstance(tags, list) or any(not isinstance(tag, str) for tag in tags):
            raise HTTPException(status_code=500, detail="A stored Community post has malformed tags; queue data is unavailable.")
        posts.append({
            "id": post.id,
            "content": post.content,
            "author_label": post.author_label,
            "post_type": post.post_type,
            "care_stage": post.care_stage,
            "tags": tags,
            "created_at": post.created_at.isoformat() if post.created_at else None,
            "moderation_status": post.moderation_status,
        })
    return {"posts": posts}


@router.patch("/team/moderation/{post_id}")
def update_team_moderation(
    post_id: str,
    payload: ModerationUpdate,
    db: Session = Depends(get_db),
):
    post = db.query(CommunityPost).filter_by(id=post_id).first()
    if not post:
        raise HTTPException(status_code=404, detail="Community post not found")
    post.moderation_status = payload.moderation_status
    db.commit()
    db.refresh(post)
    return {"id": post.id, "moderation_status": post.moderation_status}
