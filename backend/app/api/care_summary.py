from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session
from typing import Dict, Any, List, Optional
from datetime import datetime, timezone

from app.core.database import get_db
from app.models.care import Person as PersonModel, Caregiver as CaregiverModel, Evidence as EvidenceModel, CareEvent as CareEventModel, Insight as InsightModel
from app.models.meaning import Meaning as MeaningModel, MeaningEvidenceLink
from app.models.behavior import BehaviorObservation as BehaviorObservationModel, BehaviorInterpretation as BehaviorInterpretationModel
from app.models.follow_up import FollowUp
from app.models.action import Action as ActionModel
from app.services.care_common import utc_now
from app.services.person_model import PersonModelEngine
from app.services.meaning import MeaningEngine
from app.services.behavior import BehaviorEngine

router = APIRouter(prefix="/summary", tags=["summary"])


def _safe_iso(dt: Optional[datetime]) -> Optional[str]:
    if dt is None:
        return None
    return dt.isoformat()


@router.get("/{person_id}", response_model=Dict[str, Any])
def get_care_summary(person_id: str, include_evidence: bool = False, db: Session = Depends(get_db)):
    person = db.query(PersonModel).filter(PersonModel.id == person_id).first()
    if not person:
        raise HTTPException(status_code=404, detail="Person not found")

    engine = PersonModelEngine(db)
    meaning_engine = MeaningEngine(db)
    behavior_engine = BehaviorEngine(db)

    profile = engine.get_profile(person_id)
    meanings = meaning_engine.list_meanings(person_id)
    observations = behavior_engine.list_observations(person_id, limit=30)
    events = (
        db.query(CareEventModel)
        .filter(CareEventModel.person_id == person_id)
        .order_by(CareEventModel.occurred_at.desc().nullslast())
        .limit(30)
        .all()
    )
    follow_ups = (
        db.query(FollowUp)
        .filter(FollowUp.person_id == person_id, FollowUp.status == "scheduled")
        .order_by(FollowUp.due_at.asc())
        .limit(10)
        .all()
    )
    actions = (
        db.query(ActionModel)
        .filter(ActionModel.person_id == person_id)
        .order_by(ActionModel.created_at.desc())
        .limit(20)
        .all()
    )
    insights = (
        db.query(InsightModel)
        .filter(InsightModel.person_id == person_id, InsightModel.is_active)
        .order_by(InsightModel.created_at.desc())
        .limit(10)
        .all()
    )

    evidence_timeline = []
    if include_evidence:
        evidence_rows = (
            db.query(EvidenceModel)
            .filter(EvidenceModel.person_id == person_id)
            .order_by(EvidenceModel.uploaded_at.desc())
            .limit(50)
            .all()
        )
        evidence_timeline = [
            {
                "id": e.id,
                "type": e.type,
                "source_text": (e.source_text or "")[:500],
                "uploaded_at": _safe_iso(e.uploaded_at),
                "uploaded_by_caregiver_id": e.uploaded_by_caregiver_id,
                "confidence": e.confidence,
                "evidence_status": e.evidence_status,
            }
            for e in evidence_rows
        ]

    recent_events = [
        {
            "id": e.id,
            "event_type": e.event_type,
            "title": e.title,
            "description": e.description,
            "occurred_at": _safe_iso(e.occurred_at),
            "tags": e.tags or [],
            "significance_verdict": e.significance_verdict,
            "attention_candidate": e.attention_candidate or False,
            "follow_up_candidate": e.follow_up_candidate or False,
            "confidence": e.confidence,
        }
        for e in events
    ]

    behavior_view = behavior_engine.behaviour_view(person_id)

    open_questions = []
    if profile:
        possible_context = getattr(profile, "possible_context", None)
        if possible_context:
            open_questions.append({
                "category": "person_profile",
                "question": possible_context,
                "evidence_ids": getattr(profile, "evidence_ids", []) or [],
            })

    for obs in observations[:10]:
        interpretation = behavior_engine.get_interpretation(obs.id)
        if interpretation and not interpretation.is_complete:
            open_questions.append({
                "category": "behavior_interpretation",
                "question": f"Interpretation for '{obs.description}' lacks a second competing explanation",
                "evidence_ids": interpretation.evidence_ids or [],
            })

    for m in meanings[:10]:
        if m.uncertainty_ids:
            open_questions.append({
                "category": "meaning_uncertainty",
                "question": f"Meaning '{m.rationale[:100] if m.rationale else m.id}' has open uncertainties",
                "evidence_ids": m.evidence_ids or [],
                "uncertainty_ids": m.uncertainty_ids or [],
            })

    summary = {
        "person": {
            "id": person.id,
            "name": person.name,
            "date_of_birth": person.date_of_birth.isoformat() if person.date_of_birth else None,
        },
        "generated_at": utc_now().isoformat(),
        "profile_completeness": profile.completeness.value if profile else "thin",
        "person_in_brief": {
            "summary": profile.summary if profile else "",
            "personalisation": getattr(profile, "personalisation", None) if profile else None,
            "what_settles_them": getattr(profile, "what_settles_them", None) if profile else [],
            "what_upsets_them": getattr(profile, "what_upsets_them", None) if profile else [],
        },
        "recent_events": recent_events,
        "evidence_trail": evidence_timeline,
        "behavior_observations": [
            {
                "id": obs.id,
                "category": obs.category.value if hasattr(obs.category, "value") else str(obs.category),
                "description": obs.description,
                "original_text": obs.original_text,
                "occurred_at": _safe_iso(obs.occurred_at),
                "is_safety_concern": obs.is_safety_concern,
            }
            for obs in observations
        ],
        "open_questions": open_questions,
        "follow_ups_due": [
            {
                "id": f.id,
                "what_to_recheck": f.what_to_recheck,
                "due_at": _safe_iso(f.due_at),
                "why_open": f.why_open,
            }
            for f in follow_ups
        ],
        "active_actions": [
            {
                "id": a.id,
                "action_type": a.action_type,
                "action_object": a.action_object,
                "normalized_action": a.normalized_action,
                "status": a.status.value if hasattr(a.status, "value") else str(a.status),
                "deadline": _safe_iso(a.deadline),
            }
            for a in actions
        ],
        "insights": [
            {
                "id": i.id,
                "title": i.title,
                "description": i.description,
                "insight_type": i.insight_type,
                "confidence": i.confidence,
                "evidence_ids": i.evidence_ids,
                "possible_context": i.possible_context,
            }
            for i in insights
        ],
        "meanings": [
            {
                "id": m.id,
                "longitudinal_pattern": m.longitudinal_pattern,
                "causal_status": m.causal_status,
                "confidence": m.confidence,
                "confidence_type": m.confidence_type,
                "rationale": m.rationale,
                "evidence_ids": m.evidence_ids,
                "not_established": m.not_established,
                "possible_context": None,
            }
            for m in meanings
        ],
        "safety_flags": {
            "attention_candidates": sum(1 for e in events if e.attention_candidate),
            "follow_up_candidates": sum(1 for e in events if e.follow_up_candidate),
            "behavior_safety_concerns": sum(1 for o in observations if o.is_safety_concern),
        },
        "_disclaimer": (
            "This is a care documentation and reasoning support record. "
            "It does not replace clinical assessment. "
            "Every summary item links back to its evidence. "
            "Uncertainty is explicit, not hidden."
        ),
    }

    return {"ok": True, "person_id": person_id, "summary": summary}
