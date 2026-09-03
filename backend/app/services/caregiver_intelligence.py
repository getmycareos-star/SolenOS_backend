from datetime import datetime, timezone
from typing import List, Optional, Dict, Any
from sqlalchemy.orm import Session
from app.models.caregiver_intelligence import (
    CaregiverProfile,
    CaregiverRole,
    CaregiverResponsibility,
    CaregiverObservation,
    CaregiverPerspective,
    CaregiverHandoff,
    CaregiverActivity,
    CaregiverCommunication,
    CareNetworkRelationship,
    ResponsibilityGap,
    PerspectiveConflict,
    DuplicateAction,
    CaregiverContext,
)
from app.schemas.caregiver_intelligence import (
    CaregiverProfileCreate,
    CaregiverRoleCreate,
    CaregiverResponsibilityCreate,
    CaregiverObservationCreate,
    CaregiverPerspectiveCreate,
    CaregiverHandoffCreate,
    CaregiverActivityCreate,
    CaregiverCommunicationCreate,
    CareNetworkRelationshipCreate,
    ResponsibilityGapCreate,
    PerspectiveConflictCreate,
    DuplicateActionCreate,
    CaregiverContextCreate,
    CaregiverSummary,
    MultiCaregiverSummary,
)
from app.core.caregiver_enums import (
    CaregiverInvolvementStatus,
    ResponsibilityStatus,
    HandoffAcceptanceStatus,
    InformationVisibility,
    PerspectiveConflictStatus,
    CaregiverActivityType,
    CoverageGapStatus,
    DuplicateActionStatus,
)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _ensure_utc(dt: Optional[datetime]) -> Optional[datetime]:
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


class CaregiverIntelligenceService:

    def __init__(self, db: Session):
        self.db = db

    def create_caregiver_profile(self, profile: CaregiverProfileCreate) -> CaregiverProfile:
        db_profile = CaregiverProfile(**profile.model_dump())
        self.db.add(db_profile)
        self.db.commit()
        self.db.refresh(db_profile)
        return db_profile

    def get_caregiver_profile(self, caregiver_id: str) -> Optional[CaregiverProfile]:
        return self.db.query(CaregiverProfile).filter(CaregiverProfile.id == caregiver_id).first()

    def list_caregiver_profiles(self, person_id: str) -> List[CaregiverProfile]:
        return self.db.query(CaregiverProfile).filter(CaregiverProfile.person_id == person_id).all()

    def update_caregiver_involvement(self, caregiver_id: str, status: CaregiverInvolvementStatus) -> Optional[CaregiverProfile]:
        profile = self.get_caregiver_profile(caregiver_id)
        if not profile:
            return None
        profile.involvement_status = status
        profile.updated_at = _utc_now()
        self.db.commit()
        self.db.refresh(profile)
        return profile

    def create_caregiver_role(self, role: CaregiverRoleCreate) -> CaregiverRole:
        db_role = CaregiverRole(**role.model_dump())
        self.db.add(db_role)
        self.db.commit()
        self.db.refresh(db_role)
        return db_role

    def get_current_roles(self, caregiver_id: str, person_id: str) -> List[CaregiverRole]:
        return (
            self.db.query(CaregiverRole)
            .filter(
                CaregiverRole.caregiver_id == caregiver_id,
                CaregiverRole.person_id == person_id,
                CaregiverRole.is_current,
            )
            .all()
        )

    def assign_responsibility(self, responsibility: CaregiverResponsibilityCreate) -> CaregiverResponsibility:
        db_resp = CaregiverResponsibility(**responsibility.model_dump())
        self.db.add(db_resp)
        self.db.commit()
        self.db.refresh(db_resp)
        self._detect_responsibility_gaps(db_resp.person_id)
        return db_resp

    def get_active_responsibilities(self, person_id: str, caregiver_id: Optional[str] = None) -> List[CaregiverResponsibility]:
        query = self.db.query(CaregiverResponsibility).filter(
            CaregiverResponsibility.person_id == person_id,
            CaregiverResponsibility.status != ResponsibilityStatus.HISTORICAL,
            CaregiverResponsibility.status != ResponsibilityStatus.GAP,
        )
        if caregiver_id:
            query = query.filter(CaregiverResponsibility.caregiver_id == caregiver_id)
        return query.all()

    def get_responsibility_history(self, person_id: str, caregiver_id: str) -> List[CaregiverResponsibility]:
        return (
            self.db.query(CaregiverResponsibility)
            .filter(
                CaregiverResponsibility.person_id == person_id,
                CaregiverResponsibility.caregiver_id == caregiver_id,
            )
            .order_by(CaregiverResponsibility.effective_start.desc())
            .all()
        )

    def record_observation(self, observation: CaregiverObservationCreate) -> CaregiverObservation:
        db_obs = CaregiverObservation(**observation.model_dump())
        self.db.add(db_obs)
        self.db.commit()
        self.db.refresh(db_obs)
        return db_obs

    def get_observations_for_caregiver(self, person_id: str, caregiver_id: str, limit: int = 50) -> List[CaregiverObservation]:
        return (
            self.db.query(CaregiverObservation)
            .filter(
                CaregiverObservation.person_id == person_id,
                CaregiverObservation.caregiver_id == caregiver_id,
            )
            .order_by(CaregiverObservation.report_time.desc())
            .limit(limit)
            .all()
        )

    def get_shared_observations(self, person_id: str, limit: int = 50) -> List[CaregiverObservation]:
        return (
            self.db.query(CaregiverObservation)
            .filter(
                CaregiverObservation.person_id == person_id,
                CaregiverObservation.is_promoted_to_shared,
            )
            .order_by(CaregiverObservation.report_time.desc())
            .limit(limit)
            .all()
        )

    def record_perspective(self, perspective: CaregiverPerspectiveCreate) -> CaregiverPerspective:
        db_persp = CaregiverPerspective(**perspective.model_dump())
        self.db.add(db_persp)
        self.db.commit()
        self.db.refresh(db_persp)
        self._detect_perspective_conflicts(db_persp.person_id)
        return db_persp

    def get_perspectives_for_caregiver(self, person_id: str, caregiver_id: str) -> List[CaregiverPerspective]:
        return (
            self.db.query(CaregiverPerspective)
            .filter(
                CaregiverPerspective.person_id == person_id,
                CaregiverPerspective.caregiver_id == caregiver_id,
            )
            .order_by(CaregiverPerspective.timestamp.desc())
            .all()
        )

    def record_handoff(self, handoff: CaregiverHandoffCreate) -> CaregiverHandoff:
        db_handoff = CaregiverHandoff(**handoff.model_dump())
        self.db.add(db_handoff)
        self.db.commit()
        self.db.refresh(db_handoff)
        return db_handoff

    def accept_handoff(self, handoff_id: str, acceptance_evidence: Optional[str] = None) -> Optional[CaregiverHandoff]:
        handoff = self.db.query(CaregiverHandoff).filter(CaregiverHandoff.id == handoff_id).first()
        if not handoff:
            return None
        handoff.acceptance_status = HandoffAcceptanceStatus.ACCEPTED
        handoff.acceptance_evidence = acceptance_evidence
        handoff.is_completed = True
        self.db.commit()
        self.db.refresh(handoff)
        return handoff

    def get_pending_handoffs(self, person_id: str, caregiver_id: Optional[str] = None) -> List[CaregiverHandoff]:
        query = self.db.query(CaregiverHandoff).filter(
            CaregiverHandoff.person_id == person_id,
            CaregiverHandoff.acceptance_status == HandoffAcceptanceStatus.UNCONFIRMED,
        )
        if caregiver_id:
            query = query.filter(CaregiverHandoff.to_caregiver_id == caregiver_id)
        return query.all()

    def record_activity(self, activity: CaregiverActivityCreate) -> CaregiverActivity:
        db_activity = CaregiverActivity(**activity.model_dump())
        self.db.add(db_activity)
        self.db.commit()
        self.db.refresh(db_activity)
        return db_activity

    def get_activity_history(self, person_id: str, caregiver_id: Optional[str] = None, limit: int = 100) -> List[CaregiverActivity]:
        query = self.db.query(CaregiverActivity).filter(CaregiverActivity.person_id == person_id)
        if caregiver_id:
            query = query.filter(CaregiverActivity.caregiver_id == caregiver_id)
        return query.order_by(CaregiverActivity.occurred_at.desc()).limit(limit).all()

    def record_communication(self, communication: CaregiverCommunicationCreate) -> CaregiverCommunication:
        db_comm = CaregiverCommunication(**communication.model_dump())
        self.db.add(db_comm)
        self.db.commit()
        self.db.refresh(db_comm)
        return db_comm

    def get_communications_for_caregiver(self, person_id: str, caregiver_id: str, limit: int = 50) -> List[CaregiverCommunication]:
        return (
            self.db.query(CaregiverCommunication)
            .filter(
                CaregiverCommunication.person_id == person_id,
                CaregiverCommunication.from_caregiver_id == caregiver_id,
            )
            .order_by(CaregiverCommunication.communication_time.desc())
            .limit(limit)
            .all()
        )

    def create_care_network_relationship(self, relationship: CareNetworkRelationshipCreate) -> CareNetworkRelationship:
        db_rel = CareNetworkRelationship(**relationship.model_dump())
        self.db.add(db_rel)
        self.db.commit()
        self.db.refresh(db_rel)
        return db_rel

    def get_care_network(self, person_id: str) -> List[CareNetworkRelationship]:
        return (
            self.db.query(CareNetworkRelationship)
            .filter(
                CareNetworkRelationship.person_id == person_id,
                CareNetworkRelationship.is_active,
            )
            .all()
        )

    def create_context(self, context: CaregiverContextCreate) -> CaregiverContext:
        db_context = CaregiverContext(**context.model_dump())
        self.db.add(db_context)
        self.db.commit()
        self.db.refresh(db_context)
        return db_context

    def get_contexts(self, person_id: str) -> List[CaregiverContext]:
        return (
            self.db.query(CaregiverContext)
            .filter(CaregiverContext.person_id == person_id)
            .order_by(CaregiverContext.effective_start.desc())
            .all()
        )

    def create_responsibility_gap(self, gap: ResponsibilityGapCreate) -> ResponsibilityGap:
        db_gap = ResponsibilityGap(**gap.model_dump())
        self.db.add(db_gap)
        self.db.commit()
        self.db.refresh(db_gap)
        return db_gap

    def get_open_gaps(self, person_id: str) -> List[ResponsibilityGap]:
        return (
            self.db.query(ResponsibilityGap)
            .filter(
                ResponsibilityGap.person_id == person_id,
                ResponsibilityGap.status == CoverageGapStatus.OPEN,
            )
            .all()
        )

    def create_perspective_conflict(self, conflict: PerspectiveConflictCreate) -> PerspectiveConflict:
        db_conflict = PerspectiveConflict(**conflict.model_dump())
        self.db.add(db_conflict)
        self.db.commit()
        self.db.refresh(db_conflict)
        return db_conflict

    def get_conflicts(self, person_id: str) -> List[PerspectiveConflict]:
        return (
            self.db.query(PerspectiveConflict)
            .filter(
                PerspectiveConflict.person_id == person_id,
                PerspectiveConflict.status == PerspectiveConflictStatus.UNRESOLVED,
            )
            .all()
        )

    def create_duplicate_action(self, duplicate: DuplicateActionCreate) -> DuplicateAction:
        db_dup = DuplicateAction(**duplicate.model_dump())
        self.db.add(db_dup)
        self.db.commit()
        self.db.refresh(db_dup)
        return db_dup

    def get_duplicate_actions(self, person_id: str) -> List[DuplicateAction]:
        return (
            self.db.query(DuplicateAction)
            .filter(DuplicateAction.person_id == person_id)
            .order_by(DuplicateAction.created_at.desc())
            .all()
        )

    def promote_observation_to_shared(self, observation_id: str, promoting_caregiver_id: str) -> Optional[CaregiverObservation]:
        observation = self.db.query(CaregiverObservation).filter(CaregiverObservation.id == observation_id).first()
        if not observation:
            return None
        if observation.information_visibility == InformationVisibility.PRIVATE:
            observation.information_visibility = InformationVisibility.SHARED
        observation.is_promoted_to_shared = True
        observation.promoted_at = _utc_now()
        observation.promoted_by_caregiver_id = promoting_caregiver_id
        self.db.commit()
        self.db.refresh(observation)
        return observation

    def promote_perspective_to_shared(self, perspective_id: str, promoting_caregiver_id: str) -> Optional[CaregiverPerspective]:
        perspective = self.db.query(CaregiverPerspective).filter(CaregiverPerspective.id == perspective_id).first()
        if not perspective:
            return None
        if perspective.information_visibility == InformationVisibility.PRIVATE:
            perspective.information_visibility = InformationVisibility.SHARED
        perspective.is_promoted_to_shared = True
        perspective.promoted_at = _utc_now()
        perspective.promoted_by_caregiver_id = promoting_caregiver_id
        self.db.commit()
        self.db.refresh(perspective)
        return perspective

    def get_caregiver_summary(self, person_id: str, caregiver_id: str) -> CaregiverSummary:
        profile = self.get_caregiver_profile(caregiver_id)
        if not profile:
            raise ValueError(f"Caregiver {caregiver_id} not found")
        roles = self.get_current_roles(caregiver_id, person_id)
        responsibilities = self.get_active_responsibilities(person_id, caregiver_id)
        observations = self.get_observations_for_caregiver(person_id, caregiver_id, limit=10)
        perspectives = self.get_perspectives_for_caregiver(person_id, caregiver_id)
        activities = self.get_activity_history(person_id, caregiver_id, limit=10)
        return CaregiverSummary(
            caregiver_id=caregiver_id,
            name=profile.name,
            relationship=profile.relationship,
            participation_category=profile.participation_category,
            involvement_status=profile.involvement_status,
            proximity_category=profile.proximity_category,
            roles=[{"id": r.id, "role_type": r.role_type.value, "scope": r.scope, "effective_start": r.effective_start.isoformat(), "effective_end": r.effective_end.isoformat() if r.effective_end else None} for r in roles],
            responsibilities=[{"id": r.id, "type": r.responsibility_type, "scope": r.scope, "status": r.status.value, "effective_start": r.effective_start.isoformat(), "effective_end": r.effective_end.isoformat() if r.effective_end else None} for r in responsibilities],
            recent_observations=[{"id": o.id, "content": o.observation_content, "report_time": o.report_time.isoformat(), "observation_type": o.observation_type.value, "is_shared": o.is_promoted_to_shared} for o in observations],
            recent_perspectives=[{"id": p.id, "claim_type": p.claim_type.value, "claim_content": p.claim_content, "timestamp": p.timestamp.isoformat()} for p in perspectives],
            recent_activities=[{"id": a.id, "activity_type": a.activity_type.value, "description": a.description, "occurred_at": a.occurred_at.isoformat()} for a in activities],
        )

    def get_multi_caregiver_summary(self, person_id: str) -> MultiCaregiverSummary:
        profiles = self.list_caregiver_profiles(person_id)
        active_responsibilities = self.get_active_responsibilities(person_id)
        active_handoffs = self.get_pending_handoffs(person_id)
        gaps = self.get_open_gaps(person_id)
        conflicts = self.get_conflicts(person_id)
        duplicates = self.get_duplicate_actions(person_id)
        caregivers = [self.get_caregiver_summary(person_id, p.id) for p in profiles]
        return MultiCaregiverSummary(
            person_id=person_id,
            caregivers=caregivers,
            active_responsibilities=[{"id": r.id, "caregiver_id": r.caregiver_id, "type": r.responsibility_type, "status": r.status.value} for r in active_responsibilities],
            active_handoffs=[{"id": h.id, "from": h.from_caregiver_id, "to": h.to_caregiver_id, "type": h.responsibility_type, "acceptance": h.acceptance_status.value} for h in active_handoffs],
            responsibility_gaps=[{"id": g.id, "type": g.responsibility_type, "status": g.status.value} for g in gaps],
            perspective_conflicts=[{"id": c.id, "caregiver_a": c.caregiver_a_id, "caregiver_b": c.caregiver_b_id, "claim_a": c.claim_a, "claim_b": c.claim_b, "status": c.status.value} for c in conflicts],
            duplicate_actions=[{"id": d.id, "caregiver_a": d.caregiver_a_id, "caregiver_b": d.caregiver_b_id, "action_type": d.action_type, "status": d.status.value} for d in duplicates],
            coverage_gaps=[],
        )

    def get_care_network_state(self, person_id: str) -> Dict[str, Any]:
        profiles = self.list_caregiver_profiles(person_id)
        active_caregiver_ids = [p.id for p in profiles if p.involvement_status == CaregiverInvolvementStatus.ACTIVE]
        responsibilities = self.get_active_responsibilities(person_id)
        handoffs = self.get_pending_handoffs(person_id)
        gaps = self.get_open_gaps(person_id)
        conflicts = self.get_conflicts(person_id)
        network = self.get_care_network(person_id)
        return {
            "person_id": person_id,
            "active_caregiver_ids": active_caregiver_ids,
            "active_caregiver_count": len(active_caregiver_ids),
            "responsibility_summary": [{"id": r.id, "caregiver_id": r.caregiver_id, "type": r.responsibility_type, "status": r.status.value} for r in responsibilities],
            "active_handoffs": [{"id": h.id, "from": h.from_caregiver_id, "to": h.to_caregiver_id, "type": h.responsibility_type, "acceptance": h.acceptance_status.value} for h in handoffs],
            "responsibility_gaps": [{"id": g.id, "type": g.responsibility_type, "status": g.status.value} for g in gaps],
            "perspective_conflicts": [{"id": c.id, "caregiver_a": c.caregiver_a_id, "caregiver_b": c.caregiver_b_id, "claim_a": c.claim_a, "claim_b": c.claim_b, "status": c.status.value} for c in conflicts],
            "communication_relationships": [{"id": r.id, "from": r.from_caregiver_id, "to": r.to_caregiver_id, "type": r.relationship_type.value, "is_active": r.is_active} for r in network],
            "unresolved_handoff_count": len([h for h in handoffs if h.acceptance_status == HandoffAcceptanceStatus.UNCONFIRMED]),
            "gap_count": len(gaps),
            "conflict_count": len(conflicts),
        }

    def _detect_responsibility_gaps(self, person_id: str) -> List[ResponsibilityGap]:
        all_active = self.get_active_responsibilities(person_id)
        assigned_types = {r.responsibility_type for r in all_active}
        gaps = []
        known_types = {"medication", "appointment", "transportation", "overnight", "financial"}
        for rtype in known_types:
            if rtype not in assigned_types:
                existing_gap = (
                    self.db.query(ResponsibilityGap)
                    .filter(
                        ResponsibilityGap.person_id == person_id,
                        ResponsibilityGap.responsibility_type == rtype,
                        ResponsibilityGap.status == CoverageGapStatus.OPEN,
                    )
                    .first()
                )
                if not existing_gap:
                    gap = ResponsibilityGapCreate(
                        person_id=person_id,
                        responsibility_type=rtype,
                        status=CoverageGapStatus.OPEN,
                        provenance="Auto-detected: no active responsibility for this type",
                    )
                    gaps.append(self.create_responsibility_gap(gap))
        return gaps

    def _detect_perspective_conflicts(self, person_id: str) -> List[PerspectiveConflict]:
        perspectives = (
            self.db.query(CaregiverPerspective)
            .filter(CaregiverPerspective.person_id == person_id)
            .order_by(CaregiverPerspective.timestamp.desc())
            .all()
        )
        conflicts = []
        seen_pairs = set()
        for i, p_a in enumerate(perspectives):
            for p_b in perspectives[i + 1:]:
                pair = tuple(sorted([p_a.caregiver_id, p_b.caregiver_id]))
                if pair in seen_pairs:
                    continue
                if p_a.caregiver_id == p_b.caregiver_id:
                    continue
                if p_a.claim_type == p_b.claim_type and p_a.claim_content != p_b.claim_content:
                    ctx_a = (
                        self.db.query(CaregiverContext)
                        .filter(CaregiverContext.caregiver_id == p_a.caregiver_id, CaregiverContext.person_id == person_id)
                        .first()
                    )
                    ctx_b = (
                        self.db.query(CaregiverContext)
                        .filter(CaregiverContext.caregiver_id == p_b.caregiver_id, CaregiverContext.person_id == person_id)
                        .first()
                    )
                    context_a = ctx_a.observation_window_start + " to " + ctx_a.observation_window_end if ctx_a and ctx_a.observation_window_start else None
                    context_b = ctx_b.observation_window_start + " to " + ctx_b.observation_window_end if ctx_b and ctx_b.observation_window_start else None
                    if context_a and context_b and context_a != context_b:
                        status = PerspectiveConflictStatus.CONTEXTUALLY_DIVERGENT
                    else:
                        status = PerspectiveConflictStatus.CONFLICTING
                    conflict = PerspectiveConflictCreate(
                        person_id=person_id,
                        caregiver_a_id=p_a.caregiver_id,
                        caregiver_b_id=p_b.caregiver_id,
                        conflict_type=f"{p_a.claim_type.value}_conflict",
                        claim_a=p_a.claim_content,
                        claim_b=p_b.claim_content,
                        context_a=context_a,
                        context_b=context_b,
                        status=status,
                    )
                    db_conflict = self.create_perspective_conflict(conflict)
                    conflicts.append(db_conflict)
                    seen_pairs.add(pair)
        return conflicts

    def detect_duplicate_actions(self, person_id: str, caregiver_a_id: str, caregiver_b_id: str, action_type: str, desc_a: str, desc_b: str) -> DuplicateAction:
        existing = (
            self.db.query(DuplicateAction)
            .filter(
                DuplicateAction.person_id == person_id,
                DuplicateAction.caregiver_a_id == caregiver_a_id,
                DuplicateAction.caregiver_b_id == caregiver_b_id,
                DuplicateAction.action_type == action_type,
                DuplicateAction.status == DuplicateActionStatus.POTENTIAL_DUPLICATE,
            )
            .first()
        )
        if existing:
            return existing
        duplicate = DuplicateActionCreate(
            person_id=person_id,
            caregiver_a_id=caregiver_a_id,
            caregiver_b_id=caregiver_b_id,
            action_type=action_type,
            action_description_a=desc_a,
            action_description_b=desc_b,
            status=DuplicateActionStatus.POTENTIAL_DUPLICATE,
        )
        return self.create_duplicate_action(duplicate)

    def record_unknown_actor_activity(self, person_id: str, activity_type: CaregiverActivityType, description: str, occurred_at: datetime, shared_account_id: Optional[str] = None) -> CaregiverActivity:
        unknown_caregiver_id = "unknown"
        activity = CaregiverActivityCreate(
            caregiver_id=unknown_caregiver_id,
            person_id=person_id,
            activity_type=activity_type,
            description=description,
            occurred_at=occurred_at,
            is_anonymous=True,
            shared_account_id=shared_account_id,
        )
        return self.record_activity(activity)

    def get_caregiver_by_email(self, email: str) -> Optional[CaregiverProfile]:
        return self.db.query(CaregiverProfile).filter(CaregiverProfile.email == email).first()

    def get_caregiver_by_name(self, person_id: str, name: str) -> List[CaregiverProfile]:
        return (
            self.db.query(CaregiverProfile)
            .filter(CaregiverProfile.person_id == person_id, CaregiverProfile.name == name)
            .all()
        )
