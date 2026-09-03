import hashlib
from difflib import SequenceMatcher
from datetime import datetime
from typing import Optional, List, Dict, Any
from sqlalchemy.orm import Session
from app.models.action import Action as ActionModel, ActionStatus
from app.schemas.action import ActionCompletionEvidence


class ActionLifecycleService:
    STATUS_TRANSITIONS = {
        "detected": ["pending_confirmation", "active", "conditional", "cancelled"],
        "pending_confirmation": ["active", "cancelled", "superseded"],
        "active": ["completed", "cancelled", "superseded", "expired", "conditional", "blocked"],
        "conditional": ["active", "cancelled", "superseded", "expired"],
        "blocked": ["active", "cancelled", "superseded"],
        "completed": ["superseded"],
        "cancelled": [],
        "superseded": [],
        "expired": [],
    }

    def transition_status(self, action: ActionModel, new_status: str, db: Session) -> ActionModel:
        allowed = self.STATUS_TRANSITIONS.get(action.status, [])
        if new_status not in allowed:
            raise ValueError(f"Cannot transition action {action.id} from {action.status} to {new_status}. Allowed: {allowed}")

        action.status = new_status
        if new_status == "cancelled":
            action.cancelled_at = datetime.utcnow()
        elif new_status == "expired":
            action.expired_at = datetime.utcnow()
        elif new_status == "completed":
            action.completed_at = datetime.utcnow()

        db.add(action)
        db.commit()
        db.refresh(action)
        return action

    def record_completion(self, action: ActionModel, evidence: ActionCompletionEvidence, db: Session) -> ActionModel:
        if action.status in ("cancelled", "superseded", "expired"):
            raise ValueError(f"Cannot complete action in status {action.status}")

        action.status = ActionStatus.COMPLETED
        action.completion_evidence_ids = evidence.evidence_ids
        action.completion_confidence = evidence.completion_confidence
        action.completed_at = datetime.utcnow()

        db.add(action)
        db.commit()
        db.refresh(action)
        return action

    def supersede_action(self, action: ActionModel, new_action_id: str, db: Session) -> ActionModel:
        if action.status == "superseded":
            raise ValueError(f"Action {action.id} is already superseded")

        action.status = ActionStatus.SUPERSEDED
        action.superseded_by_action_id = new_action_id

        db.add(action)
        db.commit()
        db.refresh(action)
        return action

    def cancel_action(self, action: ActionModel, db: Session) -> ActionModel:
        return self.transition_status(action, ActionStatus.CANCELLED, db)

    def expire_action(self, action: ActionModel, db: Session) -> ActionModel:
        return self.transition_status(action, ActionStatus.EXPIRED, db)

    def activate_action(self, action: ActionModel, db: Session) -> ActionModel:
        return self.transition_status(action, ActionStatus.ACTIVE, db)

    def block_action(self, action: ActionModel, db: Session) -> ActionModel:
        return self.transition_status(action, ActionStatus.BLOCKED, db)

    def check_expiration(self, action: ActionModel, reference_time: datetime = None) -> bool:
        if reference_time is None:
            reference_time = datetime.utcnow()

        if action.status != "active":
            return False

        if action.deadline and reference_time > action.deadline:
            return True
        return False

    def verify_completion_evidence(self, action: ActionModel, evidence_ids: List[str], db: Session) -> Dict[str, Any]:
        if not evidence_ids:
            return {"verified": False, "reason": "No evidence provided"}

        from app.models.care import Evidence as EvidenceModel
        evidence_items = db.query(EvidenceModel).filter(EvidenceModel.id.in_(evidence_ids)).all()

        if not evidence_items:
            return {"verified": False, "reason": "No valid evidence found"}

        evidence_strengths = []
        for ev in evidence_items:
            if ev.type in ("appointment_record", "provider_note", "lab_result", "medication_list"):
                evidence_strengths.append(0.9)
            elif ev.type in ("caregiver_report", "patient_report"):
                evidence_strengths.append(0.6)
            elif ev.type in ("document", "form"):
                evidence_strengths.append(0.7)
            else:
                evidence_strengths.append(0.4)

        max_strength = max(evidence_strengths) if evidence_strengths else 0.0
        return {
            "verified": max_strength >= 0.5,
            "confidence": max_strength,
            "evidence_count": len(evidence_items),
            "reason": "Evidence supports completion" if max_strength >= 0.5 else "Evidence too weak to confirm completion",
        }


class ActionIdentityService:
    def compute_identity_hash(self, action: ActionModel) -> str:
        parts = [
            action.person_id,
            action.action_type,
            action.action_object or "",
            action.normalized_action,
            str(action.is_explicit),
        ]
        raw = "|".join(parts).lower().strip()
        return hashlib.sha256(raw.encode()).hexdigest()[:16]

    def similarity(self, a: str, b: str) -> float:
        return SequenceMatcher(None, a.lower(), b.lower()).ratio()

    def is_duplicate(self, action_a: ActionModel, action_b: ActionModel, threshold: float = 0.85) -> Dict[str, Any]:
        hash_a = self.compute_identity_hash(action_a)
        hash_b = self.compute_identity_hash(action_b)

        if hash_a == hash_b:
            return {"is_duplicate": True, "confidence": 1.0, "match_type": "exact"}

        similarity = self.similarity(
            action_a.normalized_action or "",
            action_b.normalized_action or "",
        )

        if similarity >= threshold:
            return {"is_duplicate": True, "confidence": similarity, "match_type": "fuzzy"}

        return {"is_duplicate": False, "confidence": similarity, "match_type": "none"}

    def find_duplicates(self, action: ActionModel, existing_actions: List[ActionModel]) -> List[Dict[str, Any]]:
        duplicates = []
        for existing in existing_actions:
            if existing.id == action.id:
                continue
            if existing.person_id != action.person_id:
                continue
            if existing.status in ("cancelled", "superseded", "expired", "completed"):
                continue
            result = self.is_duplicate(action, existing)
            if result["is_duplicate"]:
                duplicates.append({
                    "existing_action_id": existing.id,
                    "confidence": result["confidence"],
                    "match_type": result["match_type"],
                })
        return duplicates

    def update_action_from_evidence(self, action: ActionModel, new_evidence_text: str, new_evidence_id: str, db: Session) -> Optional[ActionModel]:
        if not action.source_passage or not new_evidence_text:
            return None

        from Levenshtein import ratio
        similarity = ratio(action.source_passage.lower(), new_evidence_text.lower())

        if similarity >= 0.7:
            if action.source_passage not in new_evidence_text and similarity < 0.95:
                action.source_passage = new_evidence_text
            if new_evidence_id not in (action.source_evidence_id or ""):
                pass
            db.add(action)
            db.commit()
            db.refresh(action)
            return action
        return None
