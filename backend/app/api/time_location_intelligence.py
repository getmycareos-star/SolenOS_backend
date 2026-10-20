from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from typing import List
from datetime import datetime
from app.core.database import get_db
from app.core.security import get_current_principal, require_care_context_access, require_authenticated, AuthenticatedPrincipal
from app.models.care_event import CareEvent as CareEventModel
from app.models.time_location import Appointment as AppointmentModel, CareWindow as CareWindowModel
from app.schemas.time_location import DailyIntelligence as DailyIntelligenceSchema
from app.services.time_intelligence import generate_daily_intelligence, utc_now
from app.core.cache_decorators import cached_response

router = APIRouter(prefix="/daily-intelligence", tags=["time_location_intelligence"])


@cached_response(ttl=300, key_prefix="daily_intelligence")
@router.get("/{person_id}", response_model=DailyIntelligenceSchema)
def get_daily_intelligence(
    person_id: str,
    principal: AuthenticatedPrincipal = Depends(get_current_principal),
    db: Session = Depends(get_db),
):
    require_authenticated(principal)
    # Authorization check: ensure the authenticated user can access the person whose intelligence is being requested
    require_care_context_access(principal, person_id, 'daily intelligence access')
    
    # Fetch events for the person
    events = db.query(CareEventModel).filter(CareEventModel.person_id == person_id).all()
    # Convert to list of dicts as expected by generate_daily_intelligence
    events_list = []
    for evt in events:
        events_list.append({
            "id": evt.id,
            "occurred_at": evt.occurred_at.isoformat() if evt.occurred_at else None,
            "title": evt.title or "",
            "status": evt.status.value if hasattr(evt.status, 'value') else str(evt.status),
            "event_type": evt.event_type.value if hasattr(evt.event_type, 'value') else str(evt.event_type),
        })
    
    # Fetch appointments for the person
    appointments = db.query(AppointmentModel).filter(AppointmentModel.person_id == person_id).all()
    appointments_list = []
    for apt in appointments:
        appointments_list.append({
            "id": apt.id,
            "scheduled_at": apt.scheduled_at.isoformat() if apt.scheduled_at else None,
            "title": apt.title,
            "location": apt.location,
            "status": apt.status.value if hasattr(apt.status, 'value') else str(apt.status),
        })
    
    # Fetch care windows for the person
    windows = db.query(CareWindowModel).filter(CareWindowModel.person_id == person_id).all()
    windows_list = []
    for w in windows:
        windows_list.append({
            "id": w.id,
            "window_type": w.window_type,
            "started_at": w.started_at.isoformat() if w.started_at else None,
            "ended_at": w.ended_at.isoformat() if w.ended_at else None,
            "ends_at": w.ends_at.isoformat() if w.ends_at else None,
            "status": w.status,
            "time_provenance": w.time_provenance,
            "extra_metadata": w.extra_metadata,
        })
    
    # Generate daily intelligence
    result = generate_daily_intelligence(
        person_id=person_id,
        events=events_list,
        appointments=appointments_list,
        windows=windows_list,
        reference_time=utc_now()
    )
    
    return result