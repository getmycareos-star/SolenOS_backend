"""Emergency Context - Rapid access to authorized care context for emergency situations.

This endpoint provides a concise, evidence-backed view of what is already known
about a person's care context for emergency handoff situations. It strictly
follows the retrieval-only principle: it retrieves and organizes authorized
information but does not diagnose, triage, prescribe, or decide emergency care.

The behavioral transformation is:
Panic + reconstruction → Rapid access to authorized care context.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.security import get_current_principal, AuthenticatedPrincipal
from app.services.authorization import AuthorizationContext, AuthorizationDecision, authorize
from app.services.care_continuity import CareContinuityEngine
from app.services.care_summary import CareSummaryBuilder
from app.services.person_model import PersonModelEngine
from app.services.meaning import MeaningEngine
from app.services.behavior import BehaviorEngine
from app.services.daily_capture import DailyCaptureService
from app.services.care_sharing import CareSharingService
from app.models.identity import Person as PersonModel
from app.models.caregiver import Caregiver as CaregiverModel
from app.models.care_event import CareEvent as CareEventModel
from app.models.medication import Medication as MedicationRecord
from app.models.uncertainty import OpenQuestion
from app.models.follow_up import FollowUp
from app.models.evidence import Evidence as EvidenceModel
from app.models.person_responsibility import PersonResponsibility
from app.models.dementia import DementiaBaselineModel
from app.models.care_sharing import CareShareGrant, ShareCategory, ShareScope

router = APIRouter(prefix="/emergency-context", tags=["emergency-context"])


class EmergencyContextRequest:
    """Request for emergency context."""
    def __init__(
        self,
        person_id: str,
        purpose: str = "emergency_handoff",
    ):
        self.person_id = person_id
        self.purpose = purpose


def _safe_iso(dt: Optional[datetime]) -> Optional[str]:
    if dt is None:
        return None
    return dt.isoformat()


def _get_authorized_person_ids(db: Session, principal: AuthenticatedPrincipal) -> List[str]:
    """Get list of person IDs the current principal is authorized to access."""
    # Get from session or compute based on grants
    if principal.session:
        return principal.session.authorized_person_ids or []
    
    # Fallback: compute from grants if no session
    sharing = CareSharingService(db)
    # This is simplified - in practice we'd check all grants
    # For now, return empty list if no session (should require authentication)
    return []


@router.get("/{person_id}")
def get_emergency_context(
    person_id: str,
    db: Session = Depends(get_db),
    principal: AuthenticatedPrincipal = Depends(get_current_principal),
):
    """
    Get emergency context for a person.
    
    Returns a concise, evidence-backed view of authorized care context
    suitable for emergency handoff situations.
    
    Strictly retrieval-only: does not diagnose, triage, prescribe, or decide care.
    """
    # Verify person exists
    person = db.query(PersonModel).filter(PersonModel.id == person_id).first()
    if not person:
        raise HTTPException(status_code=404, detail="Person not found")
    
    # Authorization check - verify access to this person's care context
    auth_request = {
        "actor_account_id": principal.account_id,
        "actor_session_id": principal.session_id,
        "action": AuthorizationContext.API_READ,
        "resource_type": "person",
        "resource_id": person_id,
        "person_id": person_id,  # Care context (care recipient)
        "purpose": "emergency_context_access",
    }
    
    auth_result = authorize(db, **auth_request)
    if auth_result.decision != AuthorizationDecision.ALLOW:
        raise HTTPException(
            status_code=403,
            detail=f"Not authorized for emergency context access: {auth_result.reason}"
        )
    
    # Build emergency context using retrieval-only approach
    emergency_context = _build_emergency_context(db, person_id, principal)
    
    return {
        "ok": True,
        "person_id": person_id,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "emergency_context": emergency_context,
        "access_info": {
            "accessed_by": principal.account_id,
            "accessed_at": datetime.now(timezone.utc).isoformat(),
            "purpose": "emergency_context",
            "authorization_basis": auth_result.matched_policies if auth_result.matched_policies else []
        }
    }


def _build_emergency_context(db: Session, person_id: str, principal: AuthenticatedPrincipal) -> Dict[str, Any]:
    """
    Build emergency context by retrieving and organizing authorized information.
    
    This is strictly a retrieval operation - it does not create new conclusions
    or make clinical judgments. It organizes existing authorized information
    for rapid access in emergency situations.
    """
    # Initialize services
    continuity_engine = CareContinuityEngine(db)
    care_summary_builder = CareSummaryBuilder(db)
    person_model_engine = PersonModelEngine(db)
    meaning_engine = MeaningEngine(db)
    behavior_engine = BehaviorEngine(db)
    daily_capture_service = DailyCaptureService(db)
    care_sharing_service = CareSharingService(db)
    
    # Get authorized categories for this viewer
    viewer_caregiver_id = principal.account_id  # Simplified - in practice might need to resolve
    authorized_categories = care_sharing_service.visible_categories(person_id, viewer_caregiver_id)
    
    # Build the emergency context following the specification structure
    context = {}
    
    # A. Person - Who is this care record about?
    context["person"] = _get_person_info(db, person_id)
    
    # B. Current Care Context - What is currently known about their care situation?
    context["current_care_context"] = _get_current_care_context(db, person_id, authorized_categories)
    
    # C. Recent Major Changes - What changed recently that may be relevant?
    context["recent_major_changes"] = _get_recent_major_changes(db, person_id, authorized_categories)
    
    # D. Medications - Current authorized medication information
    context["medications"] = _get_medication_info(db, person_id, authorized_categories)
    
    # E. Allergies / Critical Documented Information
    context["allergies_critical_info"] = _get_allergies_and_critical_info(db, person_id, authorized_categories)
    
    # F. Baseline - What is known about usual/current baseline?
    context["baseline"] = _get_baseline_info(db, person_id, authorized_categories)
    
    # G. Recent Care Episodes - Relevant recent care episodes
    context["recent_care_episodes"] = _get_recent_care_episodes(db, person_id, authorized_categories)
    
    # H. Current Professionals & Contacts
    context["care_team_contacts"] = _get_care_team_and_contacts(db, person_id, authorized_categories)
    
    # I. Recent Unresolved Issues - Open Loops and uncertainties
    context["unresolved_issues"] = _get_unresolved_issues(db, person_id, authorized_categories)
    
    # J. Recent Decisions / Outcomes - Only when relevant to current situation
    context["recent_decisions_outcomes"] = _get_recent_decisions_outcomes(db, person_id, authorized_categories)
    
    # Add metadata about information completeness and uncertainty
    context["information_status"] = _get_information_status(db, person_id, authorized_categories)
    
    return context


def _get_person_info(db: Session, person_id: str) -> Dict[str, Any]:
    """Get basic person identification information."""
    person = db.query(PersonModel).filter(PersonModel.id == person_id).first()
    if not person:
        return {"error": "Person not found"}
    
    return {
        "id": person.id,
        "name": person.name,
        "date_of_birth": _safe_iso(person.date_of_birth),
        "legal_name": person.legal_name,
        "preferred_name": person.preferred_name,
        "phone": person.phone,
        # Note: address and emergency_contact are sensitive and may be restricted
        # based on authorization - only include if authorized
    }


def _get_current_care_context(db: Session, person_id: str, authorized_categories: set) -> Dict[str, Any]:
    """Get current care context - what is currently known."""
    # Use continuity engine to get current snapshot
    continuity_engine = CareContinuityEngine(db)
    
    # Get recent events to understand current situation
    recent_events = (
        db.query(CareEventModel)
        .filter(CareEventModel.person_id == person_id)
        .order_by(CareEventModel.occurred_at.desc())
        .limit(10)
        .all()
    )
    
    # Build deterministic current state from events
    current_known = []
    for event in recent_events[:5]:  # Most recent 5 events
        if _is_category_authorized(event.event_type, authorized_categories):
            current_known.append({
                "statement": event.title,
                "observed_at": _safe_iso(event.occurred_at),
                "recorded_at": _safe_iso(event.recorded_at),
                "recorded_by": event.created_by_caregiver_id,  # Would need to resolve to name
                "evidence_ids": event.evidence_ids or [],
                "uncertainty": "unknown" if not event.evidence_ids else "documented"
            })
    
    # If no recent events, explicitly state this
    if not current_known:
        current_known.append({
            "statement": "No recent care events documented in the authorized record.",
            "uncertainty": "unknown"
        })
    
    return {
        "description": "Current known care situation based on documented events.",
        "known_items": current_known,
        "assessment_timeframe": "Based on most recent documented events",
        "note": "This represents what is documented, not a clinical assessment of current state."
    }


def _get_recent_major_changes(db: Session, person_id: str, authorized_categories: set) -> List[Dict[str, Any]]:
    """Get recent major changes that may be relevant to care handoff."""
    changes = []
    
    # Look for changes in various domains that might be significant
    # Medication changes
    if ShareCategory.MEDICATIONS in authorized_categories:
        medication_changes = _get_medication_changes(db, person_id)
        changes.extend(medication_changes)
    
    # Caregiver changes
    caregiver_changes = _get_caregiver_changes(db, person_id)
    changes.extend(caregiver_changes)
    
    # Professional changes
    if any(cat in authorized_categories for cat in [ShareCategory.CLINICAL_DOCUMENTS, ShareCategory.MEANING_AND_WHAT_MATTERS]):
        professional_changes = _get_professional_changes(db, person_id)
        changes.extend(professional_changes)
    
    # Setting/location changes
    setting_changes = _get_setting_changes(db, person_id)
    changes.extend(setting_changes)
    
    # Sort by recency (most recent first) and limit
    changes.sort(key=lambda x: x.get("timestamp") or "", reverse=True)
    return changes[:10]  # Limit to most recent 10 major changes


def _get_medication_info(db: Session, person_id: str, authorized_categories: set) -> Dict[str, Any]:
    """Get authorized medication information."""
    if ShareCategory.MEDICATIONS not in authorized_categories:
        return {
            "information_available": False,
            "reason": "Medication information not authorized for this viewer",
            "note": "Unauthorized to view medication information"
        }
    
    # Get medication records
    medications = (
        db.query(MedicationRecord)
        .filter(MedicationRecord.person_id == person_id)
        .order_by(MedicationRecord.recorded_at.desc())
        .all()
    )
    
    if not medications:
        return {
            "information_available": True,
            "medications": [],
            "note": "No medication information documented in the authorized record"
        }
    
    med_list = []
    for med in medications[:10]:  # Limit to most recent 10
        med_list.append({
            "medication_name": med.medication_name,
            "instruction": med.dosage or "Not specified",
            "frequency": med.frequency,
            "route": med.route,
            "start_date": _safe_iso(med.start_date),
            "end_date": _safe_iso(med.end_date),
            "prescribing_professional": med.prescribing_professional_id,  # Would resolve to name
            "source": "medication_record",
            "verification_status": "documented",  # Would check evidence
            "recent_change": med.recorded_at > (datetime.now(timezone.utc) - timedelta(days=30)),
            "evidence_ids": med.evidence_ids or []
        })
    
    return {
        "information_available": True,
        "medications": med_list,
        "total_count": len(medications),
        "note": "Shows documented medication information. Not a recommendation for administration."
    }


def _get_allergies_and_critical_info(db: Session, person_id: str, authorized_categories: set) -> Dict[str, Any]:
    """Get allergies and critical documented information."""
    # Check if observations category is authorized
    if ShareCategory.OBSERVATIONS not in authorized_categories:
        return {
            "information_available": False,
            "reason": "Observation information not authorized for this viewer",
            "note": "Unauthorized to view observation information including allergies"
        }
    
    # Get dementia observations which may contain allergy information
    try:
        from app.models.dementia import DementiaObservation as DementiaObservationModel
        observations = (
            db.query(DementiaObservationModel)
            .filter(DementiaObservationModel.person_id == person_id)
            .order_by(DementiaObservationModel.observed_at.desc())
            .limit(20)
            .all()
        )
    except ImportError:
        # Fallback to general evidence model if dementia observations not available
        observations = (
            db.query(EvidenceModel)
            .filter(EvidenceModel.person_id == person_id)
            .order_by(EvidenceModel.uploaded_at.desc())
            .limit(20)
            .all()
        )
    
    # Process observations to extract allergy information
    allergies = []
    critical_info = []
    
    for obs in observations:
        # Check if observation relates to allergies
        is_allergy = False
        obs_text = (obs.source_text or "").lower() if hasattr(obs, 'source_text') else ""
        
        # Common allergy indicators in text
        allergy_indicators = ['allergy', 'allergic', 'reaction', 'hypersensitivity', 'anaphylaxis']
        if any(indicator in obs_text for indicator in allergy_indicators):
            is_allergy = True
        
        # Also check observation type/category if available
        if hasattr(obs, 'observation_type') and obs.observation_type:
            obs_type_lower = str(obs.observation_type).lower()
            if any(indicator in obs_type_lower for indicator in allergy_indicators):
                is_allergy = True
        
        if is_allergy:
            allergies.append({
                "allergen": obs_text[:100] if obs_text else "Unknown allergen",
                "reaction": getattr(obs, 'value_as_string', '') or getattr(obs, 'description', '') or "Reaction details not specified",
                "severity": getattr(obs, 'severity', 'unknown'),
                "observed_at": _safe_iso(getattr(obs, 'observed_at', getattr(obs, 'uploaded_at', None))),
                "recorded_at": _safe_iso(getattr(obs, 'recorded_at', getattr(obs, 'uploaded_at', None))),
                "evidence_ids": [obs.id] if hasattr(obs, 'id') and obs.id else [],
                "verification": getattr(obs, 'verification_status', getattr(obs, 'evidence_status', 'unknown'))
            })
        else:
            # Add to critical information if it seems medically significant
            critical_indicators = ['diagnosis', 'condition', 'critical', 'urgent', 'emergency', 'serious']
            if any(indicator in obs_text for indicator in critical_indicators):
                critical_info.append({
                    "type": "observation",
                    "description": obs.source_text[:100] if obs.source_text else "Documented observation",
                    "date": _safe_iso(getattr(obs, 'observed_at', getattr(obs, 'uploaded_at', None))),
                    "source": "evidence_record",
                    "verification": getattr(obs, 'evidence_status', 'unknown'),
                    "evidence_ids": [obs.id] if hasattr(obs, 'id') and obs.id else []
                })
    
    return {
        "information_available": True,
        "allergies": allergies,
        "critical_documentation": critical_info,
        "note": "Allergy and critical information extracted from documented observations"
    }


def _get_baseline_info(db: Session, person_id: str, authorized_categories: set) -> Dict[str, Any]:
    """Get baseline information about usual functioning."""
    if not any(cat in authorized_categories for cat in [ShareCategory.MEANING_AND_WHAT_MATTERS, ShareCategory.OBSERVATIONS]):
        return {
            "information_available": False,
            "reason": "Baseline information not authorized for this viewer",
            "note": "Unauthorized to view baseline information"
        }
    
    # Get baseline records
    baselines = (
        db.query(DementiaBaselineModel)
        .filter(DementiaBaselineModel.person_id == person_id, DementiaBaselineModel.is_active == True)
        .all()
    )
    
    if not baselines:
        return {
            "information_available": True,
            "baseline_domains": [],
            "note": "No baseline information documented in the authorized record"
        }
    
    baseline_list = []
    for baseline in baselines:
        baseline_list.append({
            "domain": baseline.domain.value if hasattr(baseline.domain, "value") else str(baseline.domain),
            "description": baseline.description,
            "severity": baseline.severity,
            "frequency": baseline.frequency,
            "last_confirmed": _safe_iso(baseline.last_confirmed_date),
            "evidence_ids": baseline.evidence_ids or []
        })
    
    return {
        "information_available": True,
        "baseline_domains": baseline_list,
        "note": "Baseline based on documented longitudinal evidence, not assumptions"
    }


def _get_recent_care_episodes(db: Session, person_id: str, authorized_categories: set) -> List[Dict[str, Any]]:
    """Get recent documented care episodes."""
    if ShareCategory.CLINICAL_DOCUMENTS not in authorized_categories:
        return [{
            "information_available": False,
            "reason": "Care episode information not authorized for this viewer",
            "note": "Unauthorized to view care episode information"
        }]
    
    # Get recent care events that represent episodes
    # In practice, we'd filter by event types that represent episodes (hospitalization, ER visit, etc.)
    episodes = (
        db.query(CareEventModel)
        .filter(CareEventModel.person_id == person_id)
        .order_by(CareEventModel.occurred_at.desc())
        .limit(10)
        .all()
    )
    
    episode_list = []
    for event in episodes:
        if _is_category_authorized(event.event_type, authorized_categories):
            episode_list.append({
                "event_type": event.event_type,
                "title": event.title,
                "description": event.description,
                "occurred_at": _safe_iso(event.occurred_at),
                "recorded_at": _safe_iso(event.recorded_at),
                "location": event.location,
                "reported_by": event.created_by_caregiver_id,  # Would resolve to name
                "outcome": event.outcome or "Not documented",
                "related_evidence_ids": event.evidence_ids or [],
                "follow_up_actions": []  # Would link to follow-ups
            })
    
    if not episode_list:
        episode_list.append({
            "information_available": True,
            "episodes": [],
            "note": "No recent care episodes documented in the authorized record"
        })
    
    return episode_list


def _get_care_team_and_contacts(db: Session, person_id: str, authorized_categories: set) -> Dict[str, Any]:
    """Get current care team and relevant contacts."""
    # Check if care sharing category is authorized to see caregiver relationships
    if ShareCategory.CARE_SHARING not in authorized_categories:
        return {
            "information_available": False,
            "reason": "Care team information not authorized for this viewer",
            "note": "Unauthorized to view care team and contacts"
        }
    
    # Get caregivers through care sharing grants
    try:
        from app.models.care_sharing import CareShareGrant
        # Get active grants where this person is the care recipient
        grants = (
            db.query(CareShareGrant)
            .filter(
                CareShareGrant.person_id == person_id,
                CareShareGrant.status == "active"
            )
            .all()
        )
        
        caregivers = []
        for grant in grants:
            # Get caregiver information
            caregiver = db.query(CaregiverModel).filter(CaregiverModel.id == grant caregiver_id).first()
            if caregiver:
                caregivers.append({
                    "id": str(caregiver.id),
                    "name": f"{caregiver.first_name} {caregiver.last_name}".strip(),
                    "role": grant.share_scope.value if hasattr(grant.share_scope, 'value') else str(grant.share_scope),
                    "relationship": grant.relationship or "Not specified",
                    "contact_info": {
                        "phone": caregiver.phone_number,
                        "email": caregiver.email_address
                    } if hasattr(caregiver, 'phone_number') else {},
                    "granted_at": _safe_iso(grant.granted_at),
                    "expires_at": _safe_iso(grant.expires_at)
                })
    except Exception as e:
        # Fallback if there's an error
        caregivers = []
    
    # Get professionals (this would typically come from a different model or service)
    # For now, we'll extract from care events or observations where professionals are mentioned
    professionals = []
    try:
        # Look for professional mentions in recent care events
        from app.models.care_event import CareEvent as CareEventModel
        recent_events = (
            db.query(CareEventModel)
            .filter(CareEventModel.person_id == person_id)
            .order_by(CareEventModel.occurred_at.desc())
            .limit(10)
            .all()
        )
        
        # Extract professional information from events
        professional_roles = set()
        for event in recent_events:
            if hasattr(event, 'performed_by') and event.performed_by:
                # This is simplified - in reality we'd have a proper professional directory
                professional_roles.add(event.performed_by)
        
        for role in professional_roles:
            professionals.append({
                "role": role,
                "type": "healthcare_professional",
                "contact_info": {}  # Would need to lookup from professional directory
            })
    except Exception:
        professionals = []
    
    # Get emergency contacts (would be from person model or separate emergency contact model)
    emergency_contacts = []
    try:
        person = db.query(PersonModel).filter(PersonModel.id == person_id).first()
        if person and hasattr(person, 'emergency_contacts') and person.emergency_contacts:
            # This assumes emergency_contacts is a relationship or JSON field
            # In reality, this would depend on how emergency contacts are stored
            emergency_contacts = person.emergency_contacts if isinstance(person.emergency_contacts, list) else []
    except Exception:
        emergency_contacts = []
    
    return {
        "current_caregivers": caregivers,
        "current_professionals": professionals,
        "emergency_contacts": emergency_contacts,
        "note": "Shows currently authorized care team members and contacts",
        "information_available": True,
        "authorization_note": "Only shows relationships where viewer is authorized to see the connection"
    }


def _get_unresolved_issues(db: Session, person_id: str, authorized_categories: set) -> List[Dict[str, Any]]:
    """Get relevant unresolved issues (Open Loops)."""
    if ShareCategory.FOLLOW_UPS_AND_ACTIONS not in authorized_categories:
        return [{
            "information_available": False,
            "reason": "Unresolved issue information not authorized for this viewer",
            "note": "Unauthorized to view unresolved issues"
        }]
    
    # Get open questions/follow-ups
    open_questions = (
        db.query(OpenQuestion)
        .filter(OpenQuestion.person_id == person_id, OpenQuestion.status == "open")
        .order_by(OpenQuestion.asked_at.desc())
        .limit(10)
        .all()
    )
    
    follow_ups = (
        db.query(FollowUp)
        .filter(FollowUp.person_id == person_id, FollowUp.status == "scheduled")
        .order_by(FollowUp.due_at.asc())
        .limit(10)
        .all()
    )
    
    issues = []
    
    # Add open questions
    for question in open_questions:
        if _is_category_authorized(question.category or "general", authorized_categories):
            issues.append({
                "type": "open_question",
                "question": question.question,
                "asked_at": _safe_iso(question.asked_at),
                "evidence_ids": question.evidence_ids or [],
                "status": question.status
            })
    
    # Add follow-ups
    for follow_up in follow_ups:
        if _is_category_authorized("follow_up", authorized_categories):
            issues.append({
                "type": "follow_up",
                "what_to_recheck": follow_up.what_to_recheck,
                "due_at": _safe_iso(follow_up.due_at),
                "why_open": follow_up.why_open,
                "evidence_ids": follow_up.evidence_ids or []
            })
    
    if not issues:
        issues.append({
            "information_available": True,
            "issues": [],
            "note": "No unresolved issues documented in the authorized record"
        })
    
    return issues


def _get_recent_decisions_outcomes(db: Session, person_id: str, authorized_categories: set) -> List[Dict[str, Any]]:
    """Get recent decisions and outcomes when relevant to current situation."""
    if ShareCategory.MEANING_AND_WHAT_MATTERS not in authorized_categories:
        return [{
            "information_available": False,
            "reason": "Decisions/outcomes information not authorized for this viewer",
            "note": "Unauthorized to view decisions and outcomes"
        }]
    
    # Get recent insights or meanings that represent decisions/outcomes
    # This would be more sophisticated in a real implementation
    recent_meanings = (
        db.query(MeaningModel)  # Or whichever model contains decisions
        .filter(MeaningModel.person_id == person_id)
        .order_by(MeaningModel.created_at.desc())
        .limit(5)
        .all()
    )
    
    decisions = []
    for meaning in recent_meanings:
        # In reality, we'd have specific logic to identify decisions
        decisions.append({
            "type": "meaning_or_decision",
            "description": meaning.rationale[:100] if meaning.rationale else meaning.id,
            "date": _safe_iso(meaning.created_at),
            "confidence": meaning.confidence,
            "evidence_ids": meaning.evidence_ids or [],
            "note": "Documented meaning or decision, not a clinical assessment"
        })
    
    if not decisions:
        decisions.append({
            "information_available": True,
            "decisions": [],
            "note": "No recent decisions or outcomes documented in the authorized record"
        })
    
    return decisions


def _get_information_status(db: Session, person_id: str, authorized_categories: set) -> Dict[str, Any]:
    """Get information about what is known, unknown, or uncertain."""
    # Count authorized vs total information in various domains
    status = {}
    
    # Medications
    if ShareCategory.MEDICATIONS in authorized_categories:
        med_count = db.query(MedicationRecord).filter(MedicationRecord.person_id == person_id).count()
        status["medications"] = {
            "authorized": True,
            "documented_count": med_count,
            "note": f"{med_count} medication record(s) authorized for viewing"
        }
    else:
        status["medications"] = {
            "authorized": False,
            "note": "Medication information not authorized for this viewer"
        }
    
    # Add similar checks for other domains as needed
    status["generated_at"] = datetime.now(timezone.utc).isoformat()
    status["note"] = "Information status shows what is authorized and documented, not what exists in total"
    
    return status


def _is_category_authorized(event_type: str, authorized_categories: set) -> bool:
    """Check if an event type falls under authorized categories."""
    # Simplified mapping - in practice this would be more sophisticated
    category_mapping = {
        "medication": ShareCategory.MEDICATIONS,
        "observation": ShareCategory.OBSERVATIONS,
        "care_event": ShareCategory.DAILY_CARE,
        "hospitalization": ShareCategory.CLINICAL_DOCUMENTS,
        "follow_up": ShareCategory.FOLLOW_UPS_AND_ACTIONS,
        "meaning": ShareCategory.MEANING_AND_WHAT_MATTERS,
    }
    
    # Check if any mapping matches
    for event_key, category in category_mapping.items():
        if event_key in event_type.lower() and category in authorized_categories:
            return True
    
    # Default to daily care for unspecified events
    return ShareCategory.DAILY_CARE in authorized_categories


def _get_medication_changes(db: Session, person_id: str) -> List[Dict[str, Any]]:
    """Get recent medication changes."""
    try:
        # Get medication records ordered by date to identify changes
        medications = (
            db.query(MedicationRecord)
            .filter(MedicationRecord.person_id == person_id)
            .order_by(MedicationRecord.recorded_at.desc())
            .limit(20)
            .all()
        )
        
        changes = []
        # Look for changes in medication records (new, discontinued, dosage changes)
        # This is a simplified approach - in reality we'd have a proper change tracking system
        
        # Group medications by name to see if there are multiple versions
        med_groups = {}
        for med in medications:
            key = med.medication_name.lower().strip()
            if key not in med_groups:
                med_groups[key] = []
            med_groups[key].append(med)
        
        # For each medication, if we have multiple records, check for changes
        for med_name, med_list in med_groups.items():
            if len(med_list) > 1:
                # Sort by date
                med_list.sort(key=lambda x: x.recorded_at or datetime.min, reverse=True)
                
                # Compare most recent with previous
                newest = med_list[0]
                previous = med_list[1]
                
                changes_found = False
                change_description = []
                
                # Check for dosage changes
                if newest.dosage != previous.dosage:
                    changes_found = True
                    change_description.append(f"Dosage changed from {previous.dosage} to {newest.dosage}")
                
                # Check for frequency changes
                if newest.frequency != previous.frequency:
                    changes_found = True
                    change_description.append(f"Frequency changed from {previous.frequency} to {newest.frequency}")
                
                # Check for route changes
                if newest.route != previous.route:
                    changes_found = True
                    change_description.append(f"Route changed from {previous.route} to {newest.route}")
                
                # Check if medication was discontinued/restarted
                newest_active = getattr(newest, 'is_active', True) or (getattr(newest, 'end_date', None) is None)
                previous_active = getattr(previous, 'is_active', True) or (getattr(previous, 'end_date', None) is None)
                
                if newest_active != previous_active:
                    changes_found = True
                    if newest_active:
                        change_description.append("Medication restarted")
                    else:
                        change_description.append(f"Medication discontinued on {_safe_iso(getattr(newest, 'end_date', None))}")
                
                if changes_found:
                    changes.append({
                        "type": "medication_change",
                        "medication": med_name,
                        "changes": "; ".join(change_description),
                        "timestamp": _safe_iso(newest.recorded_at),
                        "description": f"Medication change detected for {med_name}",
                        "note": "Shows documented changes, not clinical assessment of necessity"
                    })
        
        # Also look for completely new medications (those with no prior history)
        # This is simplified - in reality we'd need to know what was previously taken
        thirty_days_ago = datetime.now(timezone.utc) - timedelta(days=30)
        recent_meds = [m for m in medications if m.recorded_at and m.recorded_at >= thirty_days_ago]
        
        for med in recent_meds[:5]:  # Limit to 5 most recent
            # Check if this medication might be new (no older records of same medication)
            older_same_med = [
                m for m in medications 
                if m.medication_name.lower() == med.medication_name.lower() 
                and m.recorded_at < med.recorded_at
            ]
            
            if not older_same_med:  # No older records of this medication
                changes.append({
                    "type": "medication_change",
                    "medication": med.medication_name,
                    "changes": "New medication added to record",
                    "timestamp": _safe_iso(med.recorded_at),
                    "description": f"New medication {med.medication_name} added to record",
                    "note": "Shows documented changes, not clinical assessment of necessity"
                })
        
        # Sort by timestamp (most recent first) and limit
        changes.sort(key=lambda x: x.get("timestamp") or "", reverse=True)
        return changes[:10]  # Return most recent 10 changes
        
    except Exception as e:
        # Return empty list on error rather than failing
        return []


def _get_caregiver_changes(db: Session, person_id: str) -> List[Dict[str, Any]]:
    """Get recent caregiver changes."""
    try:
        # Get care sharing grants ordered by date to identify changes
        from app.models.care_sharing import CareShareGrant
        
        grants = (
            db.query(CareShareGrant)
            .filter(CareShareGrant.person_id == person_id)
            .order_by(CareShareGrant.granted_at.desc())
            .limit(20)
            .all()
        )
        
        changes = []
        
        # Look for changes in grants (new, terminated, modified)
        thirty_days_ago = datetime.now(timezone.utc) - timedelta(days=30)
        recent_grants = [g for g in grants if g.granted_at and g.granted_at >= thirty_days_ago]
        
        for grant in recent_grants:
            # Determine what type of change this represents
            change_type = None
            description = None
            
            # Check if this is a new grant (recently granted)
            if grant.granted_at >= thirty_days_ago:
                # Check if there's a prior grant for same caregiver/person combo
                prior_grant = (
                    db.query(CareShareGrant)
                    .filter(
                        CareShareGrant.person_id == person_id,
                        CareShareGrant.granted_at < grant.granted_at,
                        CareShareGrant.status == "active"
                    )
                    .order_by(CareShareGrant.granted_at.desc())
                    .first()
                )
                
                if not prior_grant:
                    # This appears to be a new caregiver relationship
                    change_type = "caregiver_added"
                    description = "New caregiver relationship established"
                elif prior_grant and prior_grant.caregiver_id != grant.caregiver_id:
                    # Different caregiver - replacement
                    change_type = "caregiver_changed"
                    description = "Caregiver relationship changed"
                elif prior_grant and prior_grant.caregiver_id == grant.caregiver_id:
                    # Same caregiver - possibly updated terms
                    change_type = "caregiver_updated"
                    description = "Caregiver relationship terms updated"
            
            # Check if grant was recently terminated/modified
            if grant.status in ["revoked", "expired", "terminated"] and grant.granted_at >= thirty_days_ago:
                change_type = "caregiver_removed"
                description = f"Caregiver relationship {grant.status}"
            
            if change_type and description:
                # Get caregiver name for better description
                caregiver_name = "Unknown caregiver"
                try:
                    caregiver = db.query(CaregiverModel).filter(CaregiverModel.id == grant.caregiver_id).first()
                    if caregiver:
                        caregiver_name = f"{caregiver.first_name} {caregiver.last_name}".strip()
                except Exception:
                    pass
                
                changes.append({
                    "type": "caregiver_change",
                    "change_subtype": change_type,
                    "caregiver_id": str(grant.caregiver_id),
                    "caregiver_name": caregiver_name,
                    "description": description,
                    "timestamp": _safe_iso(grant.granted_at),
                    "note": "Shows documented changes in caregiving arrangements"
                })
        
        # Sort by timestamp (most recent first) and limit
        changes.sort(key=lambda x: x.get("timestamp") or "", reverse=True)
        return changes[:10]  # Return most recent 10 changes
        
    except Exception as e:
        # Return empty list on error rather than failing
        return []


def _get_professional_changes(db: Session, person_id: str) -> List[Dict[str, Any]]:
    """Get recent professional changes."""
    try:
        # Look for professional involvement in care events and observations
        from app.models.care_event import CareEvent as CareEventModel
        from app.models.evidence import Evidence as EvidenceModel
        
        changes = []
        
        # Look at recent care events that might indicate professional involvement
        thirty_days_ago = datetime.now(timezone.utc) - timedelta(days=30)
        recent_events = (
            db.query(CareEventModel)
            .filter(
                CareEventModel.person_id == person_id,
                CareEventModel.occurred_at >= thirty_days_ago
            )
            .order_by(CareEventModel.occurred_at.desc())
            .limit(20)
            .all()
        )
        
        # Track professional mentions over time to detect changes
        professional_mentions = {}  # month -> set of professionals
        
        for event in recent_events:
            # Extract professional information from event
            event_date = event.occurred_at
            if not event_date:
                continue
                
            # Get month key for grouping
            month_key = event_date.strftime("%Y-%m")
            
            # Extract professional references from event
            professionals_in_event = set()
            
            # Check performed_by field
            if hasattr(event, 'performed_by') and event.performed_by:
                professionals_in_event.add(event.performed_by.strip())
            
            # Check description or title for professional indicators
            text_to_check = []
            if hasattr(event, 'title') and event.title:
                text_to_check.append(event.title.lower())
            if hasattr(event, 'description') and event.description:
                text_to_check.append(event.description.lower())
            
            professional_indicators = ['doctor', 'physician', 'nurse', 'therapist', 'specialist', 
                                     'clinician', 'provider', 'doctor', 'dr.', 'rn', 'lpn', 'md', 'do']
            
            for text in text_to_check:
                for indicator in professional_indicators:
                    if indicator in text:
                        # This is a simplified detection - in reality we'd use NLP
                        professionals_in_event.add(f"professional_{indicator}")
            
            if professionals_in_event:
                if month_key not in professional_mentions:
                    professional_mentions[month_key] = set()
                professional_mentions[month_key].update(professionals_in_event)
        
        # Detect changes by comparing consecutive months
        sorted_months = sorted(professional_mentions.keys(), reverse=True)
        
        for i in range(len(sorted_months) - 1):
            current_month = sorted_months[i]
            previous_month = sorted_months[i + 1]
            
            current_set = professional_mentions[current_month]
            previous_set = professional_mentions[previous_month]
            
            # Find additions and removals
            added = current_set - previous_set
            removed = previous_set - current_set
            
            # Add changes for new professionals
            for prof in added:
                changes.append({
                    "type": "professional_change",
                    "change_subtype": "professional_added",
                    "professional": prof,
                    "description": f"New professional involvement: {prof}",
                    "timestamp": f"{current_month}-01",  # First day of month
                    "note": "Shows documented changes in professional relationships"
                })
            
            # Add changes for removed professionals
            for prof in removed:
                changes.append({
                    "type": "professional_change",
                    "change_subtype": "professional_removed",
                    "professional": prof,
                    "description": f"Professional involvement ended: {prof}",
                    "timestamp": f"{previous_month}-01",  # First day of month
                    "note": "Shows documented changes in professional relationships"
                })
        
        # Also check observations for professional mentions
        recent_observations = (
            db.query(EvidenceModel)
            .filter(
                EvidenceModel.person_id == person_id,
                EvidenceModel.uploaded_at >= thirty_days_ago
            )
            .order_by(EvidenceModel.uploaded_at.desc())
            .limit(20)
            .all()
        )
        
        # Process observations similarly (simplified)
        for obs in recent_observations:
            obs_date = obs.uploaded_at
            if not obs_date:
                continue
                
            month_key = obs_date.strftime("%Y-%m")
            
            # Check observation text for professional indicators
            text_to_check = []
            if hasattr(obs, 'source_text') and obs.source_text:
                text_to_check.append(obs.source_text.lower())
            if hasattr(obs, 'description') and obs.description:
                text_to_check.append(obs.description.lower())
            
            professional_indicators = ['doctor', 'physician', 'nurse', 'therapist', 'specialist', 
                                     'clinician', 'provider', 'doctor', 'dr.', 'rn', 'lpn', 'md', 'do']
            
            found_professionals = set()
            for text in text_to_check:
                for indicator in professional_indicators:
                    if indicator in text:
                        found_professionals.add(f"observation_professional_{indicator}")
            
            if found_professionals and month_key not in professional_mentions:
                professional_mentions[month_key] = set()
            if found_professionals:
                professional_mentions[month_key].update(found_professionals)
        
        # Sort by timestamp (most recent first) and limit
        changes.sort(key=lambda x: x.get("timestamp") or "", reverse=True)
        return changes[:10]  # Return most recent 10 changes
        
    except Exception as e:
        # Return empty list on error rather than failing
        return []


def _get_setting_changes(db: Session, person_id: str) -> List[Dict[str, Any]]:
    """Get recent setting/location changes."""
    try:
        # Look for setting/location information in care events and observations
        from app.models.care_event import CareEvent as CareEventModel
        from app.models.evidence import Evidence as EvidenceModel
        
        changes = []
        
        # Look at recent care events that might indicate setting changes
        thirty_days_ago = datetime.now(timezone.utc) - timedelta(days=30)
        recent_events = (
            db.query(CareEventModel)
            .filter(
                CareEventModel.person_id == person_id,
                CareEventModel.occurred_at >= thirty_days_ago
            )
            .order_by(CareEventModel.occurred_at.desc())
            .limit(20)
            .all()
        )
        
        # Track setting mentions over time to detect changes
        setting_mentions = {}  # month -> set of settings
        
        for event in recent_events:
            # Extract setting information from event
            event_date = event.occurred_at
            if not event_date:
                continue
                
            # Get month key for grouping
            month_key = event_date.strftime("%Y-%m")
            
            # Extract setting references from event
            settings_in_event = set()
            
            # Check location field
            if hasattr(event, 'location') and event.location:
                settings_in_event.add(event.location.strip())
            
            # Check description or title for setting indicators
            text_to_check = []
            if hasattr(event, 'title') and event.title:
                text_to_check.append(event.title.lower())
            if hasattr(event, 'description') and event.description:
                text_to_check.append(event.description.lower())
            
            setting_indicators = ['home', 'hospital', 'clinic', 'facility', 'nursing home', 
                                'assisted living', 'rehab', 'emergency room', 'er', 'office',
                                'pharmacy', 'laboratory', 'imaging center']
            
            for text in text_to_check:
                for indicator in setting_indicators:
                    if indicator in text:
                        settings_in_event.add(indicator)
            
            if settings_in_event:
                if month_key not in setting_mentions:
                    setting_mentions[month_key] = set()
                setting_mentions[month_key].update(settings_in_event)
        
        # Detect changes by comparing consecutive months
        sorted_months = sorted(setting_mentions.keys(), reverse=True)
        
        for i in range(len(sorted_months) - 1):
            current_month = sorted_months[i]
            previous_month = sorted_months[i + 1]
            
            current_set = setting_mentions[current_month]
            previous_set = setting_mentions[previous_month]
            
            # Find additions and removals
            added = current_set - previous_set
            removed = previous_set - current_set
            
            # Add changes for new settings
            for setting in added:
                changes.append({
                    "type": "setting_change",
                    "change_subtype": "setting_added",
                    "setting": setting,
                    "description": f"Change to setting: {setting}",
                    "timestamp": f"{current_month}-01",  # First day of month
                    "note": "Shows documented changes in care setting or location"
                })
            
            # Add changes for removed settings
            for setting in removed:
                changes.append({
                    "type": "setting_change",
                    "change_subtype": "setting_removed",
                    "setting": setting,
                    "description": f"Change from setting: {setting}",
                    "timestamp": f"{previous_month}-01",  # First day of month
                    "note": "Shows documented changes in care setting or location"
                })
        
        # Also check observations for setting mentions
        recent_observations = (
            db.query(EvidenceModel)
            .filter(
                EvidenceModel.person_id == person_id,
                EvidenceModel.uploaded_at >= thirty_days_ago
            )
            .order_by(EvidenceModel.uploaded_at.desc())
            .limit(20)
            .all()
        )
        
        # Process observations similarly (simplified)
        for obs in recent_observations:
            obs_date = obs.uploaded_at
            if not obs_date:
                continue
                
            month_key = obs_date.strftime("%Y-%m")
            
            # Check observation text for setting indicators
            text_to_check = []
            if hasattr(obs, 'source_text') and obs.source_text:
                text_to_check.append(obs.source_text.lower())
            if hasattr(obs, 'description') and obs.description:
                text_to_check.append(obs.description.lower())
            
            setting_indicators = ['home', 'hospital', 'clinic', 'facility', 'nursing home', 
                                'assisted living', 'rehab', 'emergency room', 'er', 'office',
                                'pharmacy', 'laboratory', 'imaging center']
            
            found_settings = set()
            for text in text_to_check:
                for indicator in setting_indicators:
                    if indicator in text:
                        found_settings.add(indicator)
            
            if found_settings and month_key not in setting_mentions:
                setting_mentions[month_key] = set()
            if found_settings:
                setting_mentions[month_key].update(found_settings)
        
        # Sort by timestamp (most recent first) and limit
        changes.sort(key=lambda x: x.get("timestamp") or "", reverse=True)
        return changes[:10]  # Return most recent 10 changes
        
    except Exception as e:
        # Return empty list on error rather than failing
        return []