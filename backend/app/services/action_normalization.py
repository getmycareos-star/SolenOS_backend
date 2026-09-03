import re
from typing import List
from app.schemas.action import ActionCandidate, ActionSafetyCheck


STRENGTHENING_PATTERNS = [
    (r"\bconsider\s+(scheduling|starting|stopping|taking|doing|arranging)\b", "consider scheduling", "schedule", "Weak recommendation detected; must not be strengthened to obligation."),
    (r"\bmay\s+benefit\s+from\b", "may benefit from", "schedule", "Inference from benefit statement; cannot become action without explicit instruction."),
    (r"\bcould\s+benefit\s+from\b", "could benefit from", "schedule", "Inference from benefit statement; cannot become action without explicit instruction."),
    (r"\bmight\s+benefit\s+from\b", "might benefit from", "schedule", "Inference from benefit statement; cannot become action without explicit instruction."),
    (r"\brecommend(?:ed|s)?\s+(?:to\s+)?(?:schedule|start|stop|take|arrange)\b", "recommended to", "schedule", "Recommendation must remain recommendation unless confirmed as required."),
    (r"\bfollow[- ]?up\s+(?:if|when|as)\b", "follow up if", "conditional", "Conditional follow-up must remain conditional, not unconditional."),
    (r"\bif\s+symptoms?\s+worsen\b", "if symptoms worsen", "conditional", "Conditional action must remain conditional until trigger verified."),
    (r"\bpatient\s+has\s+diabetes\b", "patient has diabetes", "none", "Medical fact; no action implied without explicit instruction."),
    (r"\bmedication\s+list\s+(?:includes|contains|shows)\b", "medication list contains", "none", "Medication mention; not an instruction to start, stop, or continue."),
    (r"\bappears\s+on\s+(?:the\s+)?medication\s+list\b", "appears on medication list", "none", "Medication presence does not constitute an instruction."),
    (r"\bno\s+(?:acute|significant|abnormal)\s+findings?\b", "no findings", "none", "Negative finding does not imply follow-up action."),
    (r"\bblood\s+pressure\s+was\b", "blood pressure was", "none", "Observation does not imply action."),
    (r"\bpatient\s+was\s+seen\b", "patient was seen", "none", "Historical observation; no action implied."),
    (r"\breturn\s+in\s+\w+\s+weeks?\b", "return in", "follow_up", "Follow-up statement detected."),
    (r"\bfollow[- ]?up\s+in\s+\w+\s+weeks?\b", "follow up in", "follow_up", "Follow-up with time reference detected."),
    (r"\bconsider\s+discussing\b", "consider discussing", "ask", "Question/recommendation; not a definite task."),
]


class ActionNormalizationService:
    def validate_and_normalize(self, candidate: ActionCandidate) -> ActionCandidate:
        warnings: List[str] = []

        normalized_action = self._normalize_text(candidate.normalized_action)
        if not self._preserves_modality(normalized_action, candidate.modality):
            warnings.append("Normalized action may have strengthened original modality.")

        if self._is_medication_mention_only(candidate.original_text):
            warnings.append("Original text is a medication mention, not an instruction. Blocking action creation.")
            candidate.normalized_action = "No action: medication mention without instruction."
            candidate.action_type = "other"
            candidate.modality = "informational"
            candidate.requires_confirmation = True
            candidate.risk_tier = "high"
            candidate.is_medication_action = False
            candidate.extraction_confidence = 0.1
            return candidate

        if self._is_benefit_inference(candidate.original_text):
            warnings.append("Benefit inference detected. Downgrading to recommendation.")
            candidate.modality = "recommended"
            candidate.requires_confirmation = True
            candidate.action_type = "other"
            candidate.normalized_action = self._build_safe_normalized_action(candidate)
            candidate.extraction_confidence = min(candidate.extraction_confidence, 0.5)
            return candidate

        if candidate.has_condition and candidate.status != "conditional":
            warnings.append("Condition detected but action not marked conditional.")
            candidate.modality = "conditional"
            candidate.status = "conditional"

        if candidate.modality == "prohibited":
            candidate.action_type = "prohibited"
            candidate.normalized_action = f"(Prohibited) {candidate.normalized_action.replace('(Prohibited) ', '')}"
            candidate.risk_tier = "high"

        if candidate.actor_type == "unknown" and candidate.modality in ("required", "prohibited"):
            warnings.append("Actor is unknown for a strong-modality action. Setting confirmation required.")
            candidate.requires_confirmation = True

        if candidate.is_medication_action and not candidate.is_explicit:
            warnings.append("Medication action inferred without explicit instruction. Setting confirmation required.")
            candidate.requires_confirmation = True
            candidate.risk_tier = "high"

        if candidate.extraction_confidence < 0.7:
            warnings.append("Low extraction confidence. Setting confirmation required.")
            candidate.requires_confirmation = True

        candidate.provenance_notes = (candidate.provenance_notes or "") + " " + " ".join(warnings)
        candidate.normalized_action = self._build_safe_normalized_action(candidate)
        return candidate

    def safety_check(self, candidate: ActionCandidate) -> ActionSafetyCheck:
        warnings: List[str] = []

        if self._is_medication_mention_only(candidate.original_text):
            return ActionSafetyCheck(
                is_safe_to_create=False,
                requires_confirmation=True,
                risk_tier="high",
                warnings=["Medication mention without instruction"],
                blocked_reason="Cannot create action from medication mention alone",
            )

        if self._is_benefit_inference(candidate.original_text):
            return ActionSafetyCheck(
                is_safe_to_create=False,
                requires_confirmation=True,
                risk_tier="moderate",
                warnings=["Benefit inference; not an explicit action"],
                blocked_reason="Cannot create action from inferred benefit without confirmation",
            )

        if candidate.actor_type == "unknown" and candidate.modality in ("required", "prohibited"):
            warnings.append("Unknown actor for required/prohibited action")

        if candidate.is_medication_action and not candidate.is_explicit:
            warnings.append("Medication action not explicitly instructed")

        if candidate.modality == "optional":
            warnings.append("Optional action; confirmation required before creating task")

        if candidate.extraction_confidence < 0.6:
            warnings.append("Very low extraction confidence")

        requires_confirmation = candidate.requires_confirmation or len(warnings) > 0
        is_safe = not any("blocked" in w.lower() for w in warnings)

        return ActionSafetyCheck(
            is_safe_to_create=is_safe,
            requires_confirmation=requires_confirmation,
            risk_tier=candidate.risk_tier,
            warnings=warnings,
        )

    def _preserves_modality(self, normalized: str, original_modality: str) -> bool:
        if original_modality == "informational":
            return "informational" in normalized.lower() or not any(k in normalized.lower() for k in ["must", "should", "schedule", "start", "stop", "take"])
        if original_modality == "recommended":
            return "(recommended)" in normalized.lower() or "recommended" in normalized.lower()
        if original_modality == "optional":
            return "(optional)" in normalized.lower() or "optional" in normalized.lower()
        if original_modality == "prohibited":
            return "(prohibited)" in normalized.lower() or "do not" in normalized.lower()
        if original_modality == "conditional":
            return "(conditional)" in normalized.lower() or "if" in normalized.lower()
        if original_modality == "required":
            return not any(k in normalized.lower() for k in ["recommended", "optional", "(recommended)", "(optional)"])
        return True

    def _normalize_text(self, text: str) -> str:
        text = re.sub(r"\s+", " ", text).strip()
        text = re.sub(r"\.+$", "", text).strip()
        return text

    def _is_medication_mention_only(self, text: str) -> bool:
        mention_patterns = [
            r"\b(?:medication|medicine|drug)\s+(?:list|includes|contains|shows|has)\b",
            r"\bappears\s+on\s+(?:the\s+)?medication\s+list\b",
            r"\b(?:currently|currently)\s+(?:taking|on|using)\b",
            r"\bmedication\s+(?:x|y|z)\s+(?:is|was)\b",
            r"\b(?:blood pressure|sugar|weight|temperature)\s+was\b",
            r"\bpatient\s+has\s+(?:diabetes|hypertension|asthma|copd|heart\s+disease)\b",
        ]
        text_lower = text.lower()
        for pattern in mention_patterns:
            if re.search(pattern, text_lower):
                if not any(marker in text_lower for marker in ["start", "stop", "continue", "increase", "decrease", "hold", "take", "switch"]):
                    return True
        return False

    def _is_benefit_inference(self, text: str) -> bool:
        patterns = [
            r"\b(?:may|could|might)\s+benefit\s+from\b",
            r"\bpatient\s+may\s+(?:benefit|need|require)\b",
            r"\bconsider\s+(?:discussing|starting|scheduling|arranging)\b",
            r"\brecommend(?:ed|s)?\s+(?:to\s+)?(?:discuss|consider|evaluate)\b",
        ]
        text_lower = text.lower()
        for pattern in patterns:
            if re.search(pattern, text_lower):
                return True
        return False

    def _build_safe_normalized_action(self, candidate: ActionCandidate) -> str:
        verb_map = {
            "schedule": "Schedule",
            "call": "Call",
            "attend": "Attend",
            "monitor": "Monitor",
            "start": "Start",
            "stop": "Stop",
            "continue": "Continue",
            "increase": "Increase",
            "decrease": "Decrease",
            "hold": "Hold",
            "take": "Take",
            "ask": "Ask",
            "obtain": "Obtain",
            "submit": "Submit",
            "bring": "Bring",
            "review": "Review",
            "confirm": "Confirm",
            "follow_up": "Follow up on",
            "refer": "Refer for",
            "arrange": "Arrange",
            "record": "Record",
            "notify": "Notify",
            "prohibited": "Do not",
            "other": "Address",
        }
        verb = verb_map.get(candidate.action_type, "Address")
        obj = candidate.action_object or "the specified item"

        modality_prefix = ""
        if candidate.modality == "recommended":
            modality_prefix = "(Recommended) "
        elif candidate.modality == "optional":
            modality_prefix = "(Optional) "
        elif candidate.modality == "prohibited":
            modality_prefix = "(Prohibited) "
        elif candidate.modality == "conditional":
            modality_prefix = "(Conditional) "

        action_str = f"{modality_prefix}{verb} {obj}"

        if candidate.has_condition and candidate.condition_text:
            action_str += f" [IF: {candidate.condition_text}]"

        return action_str
