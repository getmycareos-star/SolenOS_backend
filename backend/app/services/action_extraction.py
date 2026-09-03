import re
from datetime import datetime, timedelta
from typing import Optional, List, Tuple
from dataclasses import dataclass

from app.schemas.action import ActionCandidate
from app.models.action import ActorType


@dataclass
class ActionPattern:
    pattern: str
    action_type: str
    modality: str
    confidence: float
    is_explicit: bool = True
    is_medication: bool = False
    risk_tier: str = "moderate"
    requires_confirmation: bool = False


class ActionExtractionEngine:
    ACTION_PATTERNS = [
        ActionPattern(r"\b(schedule|scheduled|book|arrange)\b.*?(appointment|follow[- ]?up|visit|consultation)", "schedule", "required", 0.9),
        ActionPattern(r"\b(call|contact|phone|reach|notify)\b.*?(doctor|physician|provider|nurse|clinic|office)", "call", "required", 0.9),
        ActionPattern(r"\b(attend|go to|be present at)\b.*?(appointment|visit|session|therapy)", "attend", "required", 0.9),
        ActionPattern(r"\b(monitor|track|measure|check|record|log)\b.*?(blood pressure|sugar|weight|temperature|symptoms|pain|glucose)", "monitor", "required", 0.85),
        ActionPattern(r"\b(start|begin|initiate)\b.*?(medication|drug|treatment|therapy|dose)", "start", "required", 0.95, is_medication=True, risk_tier="high"),
        ActionPattern(r"\b(stop|discontinue|halt|end)\b.*?(medication|drug|treatment|dose)", "stop", "required", 0.95, is_medication=True, risk_tier="high"),
        ActionPattern(r"\b(continue|keep taking|remain on|stay on)\b.*?(medication|drug|treatment)", "continue", "required", 0.9, is_medication=True, risk_tier="high"),
        ActionPattern(r"\b(increase|raise|uptitrate)\b.*?(dose|dosage|mg|medication)", "increase", "required", 0.95, is_medication=True, risk_tier="high"),
        ActionPattern(r"\b(decrease|lower|reduce|downtitrate)\b.*?(dose|dosage|mg|medication)", "decrease", "required", 0.95, is_medication=True, risk_tier="high"),
        ActionPattern(r"\b(hold|withhold|pause|skip)\b.*?(medication|drug|dose)", "hold", "required", 0.9, is_medication=True, risk_tier="high"),
        ActionPattern(r"\b(take|administer|give|use)\b.*?(medication|drug|pill|tablet|capsule|dose)", "take", "required", 0.85, is_medication=True, risk_tier="high"),
        ActionPattern(r"\b(ask|discuss|speak to|talk to|inquire)\b.*?(doctor|physician|provider|nurse|pharmacist)", "ask", "recommended", 0.8),
        ActionPattern(r"\b(obtain|get|pick up|fill|refill)\b.*?(prescription|medication|referral|document|form)", "obtain", "required", 0.85),
        ActionPattern(r"\b(complete|finish)\b.*?(blood work|lab work|test|paperwork|form)", "obtain", "required", 0.8),
        ActionPattern(r"\b(submit|file|send|return)\b.*?(form|document|paperwork|insurance|claim)", "submit", "required", 0.85),
        ActionPattern(r"\b(bring|bring in|bring with)\b.*?(document|list|records|insurance|card|medication)", "bring", "required", 0.85),
        ActionPattern(r"\b(review|read|go over|check)\b.*?(results|report|discharge|instructions|document)", "review", "recommended", 0.8),
        ActionPattern(r"\b(confirm|verify|ensure|make sure)\b.*?(appointment|medication|dose|follow[- ]?up)", "confirm", "required", 0.85),
        ActionPattern(r"\b(follow[- ]?up|followup)\b", "follow_up", "required", 0.85),
        ActionPattern(r"\b(refer|referred)\b.*?(specialist|therapy|clinic|center|physician)", "refer", "required", 0.85),
        ActionPattern(r"\b(arrange|coordinate|set up)\b.*?(transportation|ride|care|support|appointment)", "arrange", "recommended", 0.8),
        ActionPattern(r"\b(record|document|log|enter)\b.*?(symptom|measurement|reading|observation)", "record", "required", 0.85),
        ActionPattern(r"\b(return|come back|go back)\b.*?(clinic|office|hospital|doctor|physician)", "follow_up", "required", 0.85),
    ]

    MODALITY_PATTERNS = [
        (r"\b(must|shall|required|mandatory|need to|have to|necessary|essential)\b", "required", 0.95),
        (r"\b(should|ought to|recommended|advised|suggested|please|consider)\b", "recommended", 0.85),
        (r"\b(could|may|might|possibly|optional|if you wish|if desired)\b", "optional", 0.7),
        (r"\b(if\s+.*?\s+then|when\s+.*?\s+|unless|provided that|conditional)\b", "conditional", 0.8),
        (r"\b(do not|don't|avoid|refrain from|never|should not|must not|hold|withhold)\b", "prohibited", 0.9),
        (r"\b(is|was|were|has been|had been|noted|recorded|observed|documented|reported)\b", "informational", 0.9),
    ]

    TIME_PATTERNS = [
        (r"\bwithin\s+(one|two|three|four|five|six|seven|eight|nine|ten)\s+(day|days|week|weeks|month|months|hour|hours)\b", 1),
        (r"\bin\s+(one|two|three|four|five|six|seven|eight|nine|ten)\s+(day|days|week|weeks|month|months|hour|hours)\b", 1),
        (r"\bwithin\s+(\d+)\s+(day|days|week|weeks|month|months|hour|hours)\b", 1),
        (r"\bin\s+(\d+)\s+(day|days|week|weeks|month|months|hour|hours)\b", 1),
        (r"\bby\s+(monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b", 0),
        (r"\bby\s+(january|february|march|april|may|june|july|august|september|october|november|december)\s+(\d+)\b", 0),
        (r"\b(\d+)\s+(day|days|week|weeks|month|months)\s+(from\s+now|after|post)\b", 1),
        (r"\btomorrow\b", 0),
        (r"\btoday\b", 0),
        (r"\bnext\s+(week|month|monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b", 0),
        (r"\bas\s+needed\b", 2),
        (r"\bprn\b", 2),
    ]

    CONDITION_PATTERNS = [
        r"\bif\s+(.+?)(?:,|\s+then|\s+contact|\s+call|\s+notify|\s+start|\s+stop|\s+take|\s+schedule)\b",
        r"\bwhen\s+(.+?)(?:,|\s+then|\s+contact|\s+call|\s+notify|\s+start|\s+stop|\s+take|\s+schedule)\b",
        r"\bunless\s+(.+?)(?:,|\s+then|\s+contact|\s+call|\s+notify|\s+start|\s+stop|\s+take|\s+schedule)\b",
    ]

    PROHIBITION_PATTERNS = [
        r"\b(do not|don't|avoid|refrain from|never|should not|must not|hold|withhold|no\s+\w+\s+should)\b",
    ]

    RECURRENCE_PATTERNS = [
        (r"\btwice\s+daily\b", "twice daily"),
        (r"\bthree\s+times\s+daily\b", "three times daily"),
        (r"\bdaily\b", "daily"),
        (r"\btwice\s+weekly\b", "twice weekly"),
        (r"\bweekly\b", "weekly"),
        (r"\bmonthly\b", "monthly"),
        (r"\bevery\s+(morning|afternoon|evening|night|bedtime)\b", None),
        (r"\bas\s+needed\b", "as needed"),
        (r"\bprn\b", "as needed"),
    ]

    def extract(self, document_text: str, source_evidence_id: str, source_document_type: str = None, source_authority: str = "unknown", reference_date: datetime = None) -> List[ActionCandidate]:
        if reference_date is None:
            reference_date = datetime.utcnow()

        candidates = []
        sentences = self._split_sentences(document_text)

        for sentence in sentences:
            if not sentence.strip():
                continue

            candidate = self._extract_from_sentence(sentence, source_evidence_id, source_document_type, source_authority, reference_date)
            if candidate:
                candidates.append(candidate)

        return candidates

    def _split_sentences(self, text: str) -> List[str]:
        text = re.sub(r",\s*(and|or)\s+", ", ", text)
        parts = re.split(r"(?<=[.!?])\s+", text)
        sentences = [s.strip() for s in parts if s.strip()]

        expanded = []
        for sentence in sentences:
            if re.search(r"\b(if|when|unless)\b", sentence.lower()):
                expanded.append(sentence)
                continue
            comma_parts = re.split(r",\s+", sentence)
            if len(comma_parts) > 1:
                for part in comma_parts:
                    part = part.strip()
                    if part:
                        part = re.sub(r"^(and|or|but|then)\s+", "", part, flags=re.IGNORECASE).strip()
                        if part:
                            expanded.append(part)
            else:
                expanded.append(sentence)

        return [s for s in expanded if s]

    def _extract_from_sentence(self, sentence: str, source_evidence_id: str, source_document_type: str, source_authority: str, reference_date: datetime) -> Optional[ActionCandidate]:
        action_type, action_confidence = self._detect_action_type(sentence)
        modality, modality_confidence = self._detect_modality(sentence)

        if action_type == "other" and modality == "informational":
            return None

        if action_type != "other" and modality == "informational":
            modality = "required"
            modality_confidence = 0.8

        text_lower = sentence.lower()
        weakeners = ["consider", "may", "might", "could", "possibly", "recommend"]
        if any(w in text_lower for w in weakeners):
            if modality in ("required", "prohibited"):
                modality = "recommended"
                modality_confidence *= 0.7
            elif modality == "informational":
                modality = "recommended"
                modality_confidence = 0.6

        if modality == "conditional" or re.search(r"\bif\s+", text_lower):
            modality = "conditional"
            modality_confidence = max(modality_confidence, 0.8)

        action_object = self._extract_action_object(sentence, action_type)
        normalized_action = self._build_normalized_action(action_type, action_object, modality)
        actor_type, actor_label = self._extract_actor(sentence)
        condition_text, trigger_text = self._extract_condition(sentence)
        deadline = self._extract_deadline(sentence, reference_date)
        is_medication = self._is_medication_related(sentence)
        is_recurring, recurrence = self._is_recurring(sentence)
        is_explicit = self._is_explicit_instruction(sentence, modality)

        confidence = min(modality_confidence, action_confidence)
        if not is_explicit:
            confidence *= 0.6

        requires_confirmation = self._requires_confirmation(modality, is_explicit, confidence, is_medication, actor_type)
        risk_tier = "high" if is_medication else ("moderate" if modality in ("required", "prohibited") else "low")

        if modality == "prohibited":
            action_type = "prohibited"

        provenance_notes = self._build_provenance_note(sentence, modality, action_type, is_explicit, confidence)

        status = modality if modality in ("conditional", "pending_confirmation", "active") else "detected"
        if requires_confirmation and status == "detected":
            status = "pending_confirmation"

        return ActionCandidate(
            source_evidence_id=source_evidence_id,
            source_passage=sentence,
            original_text=sentence,
            extraction_confidence=confidence,
            actor_type=actor_type,
            actor_label=actor_label,
            action_type=action_type,
            action_object=action_object,
            normalized_action=normalized_action,
            modality=modality,
            condition_text=condition_text,
            trigger_text=trigger_text,
            deadline=deadline,
            start_time=None,
            duration=None,
            recurrence=recurrence,
            is_explicit=is_explicit,
            is_medication_action=is_medication,
            risk_tier=risk_tier,
            requires_confirmation=requires_confirmation,
            has_condition=bool(condition_text),
            is_recurring=is_recurring,
            status=status,
            provenance_notes=provenance_notes,
        )

    def _detect_modality(self, text: str) -> Tuple[str, float]:
        text_lower = text.lower()
        for pattern, modality, confidence in self.MODALITY_PATTERNS:
            if re.search(pattern, text_lower):
                return modality, confidence
        return "informational", 0.5

    def _detect_action_type(self, text: str) -> Tuple[str, float]:
        text_lower = text.lower()
        best_type = "other"
        best_confidence = 0.3

        for ap in self.ACTION_PATTERNS:
            if re.search(ap.pattern, text_lower):
                if ap.confidence > best_confidence:
                    best_type = ap.action_type
                    best_confidence = ap.confidence

        return best_type, best_confidence

    def _extract_action_object(self, text: str, action_type: str) -> Optional[str]:
        object_patterns = {
            "schedule": r"schedule.*?(appointment|follow[- ]?up|visit|consultation).*?(?:with|for|to see)?\s*(.+?)(?:\.|$|\s+in\s+)",
            "call": r"call.*?(doctor|physician|provider|nurse|clinic|office).*?(?:if|when|about|regarding)?\s*(.+?)(?:\.|$)",
            "attend": r"attend.*?(appointment|visit|session|therapy).*?(?:with|for|at)?\s*(.+?)(?:\.|$|\s+on\s+)",
            "monitor": r"monitor.*?(blood pressure|sugar|weight|temperature|symptoms|pain|glucose).*?(.+?)(?:\.|$)",
            "follow_up": r"follow[- ]?up.*?(?:with|for|on|about)?\s*(.+?)(?:\.|$|\s+in\s+)",
            "start": r"start.*?(medication|drug|treatment|therapy).*?(.+?)(?:\.|$)",
            "stop": r"stop.*?(medication|drug|treatment|dose).*?(.+?)(?:\.|$)",
        }

        text_lower = text.lower()
        if action_type in object_patterns:
            match = re.search(object_patterns[action_type], text_lower)
            if match:
                return match.group(1).strip().rstrip(".")

        action_nouns = ["appointment", "visit", "consultation", "medication", "drug", "treatment", "test", "referral", "form", "document", "therapy", "session", "follow-up", "clinic"]
        for noun in action_nouns:
            if noun in text_lower:
                idx = text_lower.find(noun)
                context = text[max(0, idx-20):idx+len(noun)+20]
                return context.strip()
        return None

    def _extract_actor(self, text: str) -> Tuple[str, str]:
        text_lower = text.lower()
        caregiver_markers = [r"\bcaregiver\b", r"\bfamily\b", r"\bdaughter\b", r"\bson\b", r"\bspouse\b", r"\bparent\b", r"\bguardian\b"]
        patient_markers = [r"\bpatient\b", r"\byou\b", r"\byour\b"]

        for marker in caregiver_markers:
            if re.search(marker, text_lower):
                return ActorType.CAREGIVER, "caregiver"

        for marker in patient_markers:
            if re.search(marker, text_lower):
                return ActorType.PATIENT, "patient"

        return ActorType.UNKNOWN, "unknown"

    def _extract_condition(self, text: str) -> Tuple[Optional[str], Optional[str]]:
        text_lower = text.lower()
        for pattern in self.CONDITION_PATTERNS:
            match = re.search(pattern, text_lower)
            if match:
                condition = match.group(1).strip()
                return condition, condition
        return None, None

    def _extract_deadline(self, text: str, reference_date: datetime) -> Optional[datetime]:
        text_lower = text.lower()

        for pattern, group_idx in self.TIME_PATTERNS:
            match = re.search(pattern, text_lower)
            if match:
                if group_idx > 0 and match.lastindex and match.lastindex >= group_idx:
                    try:
                        amount_str = match.group(group_idx)
                        amount = self._word_to_number(amount_str)
                        unit = match.group(group_idx + 1) if match.lastindex >= group_idx + 1 else "day"
                        delta = self._amount_to_timedelta(amount, unit)
                        return reference_date + delta
                    except (ValueError, IndexError):
                        pass
                if group_idx == 0:
                    return self._parse_absolute_time(text_lower, reference_date)

        if "as needed" in text_lower or "prn" in text_lower:
            return None

        return None

    def _word_to_number(self, word: str) -> int:
        word_map = {
            "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
            "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
        }
        lowered = word.lower()
        if lowered in word_map:
            return word_map[lowered]
        return int(word)

    def _amount_to_timedelta(self, amount: int, unit: str) -> timedelta:
        unit_map = {
            "hour": timedelta(hours=amount),
            "hours": timedelta(hours=amount),
            "day": timedelta(days=amount),
            "days": timedelta(days=amount),
            "week": timedelta(weeks=amount),
            "weeks": timedelta(weeks=amount),
            "month": timedelta(days=amount * 30),
            "months": timedelta(days=amount * 30),
        }
        return unit_map.get(unit, timedelta(days=amount))

    def _parse_absolute_time(self, text: str, reference_date: datetime) -> Optional[datetime]:
        weekdays = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
        months = ["january", "february", "march", "april", "may", "june", "july", "august", "september", "october", "november", "december"]

        for day in weekdays:
            if day in text:
                current_weekday = reference_date.weekday()
                target_weekday = weekdays.index(day)
                days_ahead = (target_weekday - current_weekday) % 7
                if days_ahead == 0:
                    days_ahead = 7
                return reference_date + timedelta(days=days_ahead)

        for i, month in enumerate(months, 1):
            if month in text:
                match = re.search(rf"{month}\s+(\d{{1,2}})", text)
                if match:
                    day = int(match.group(1))
                    year = reference_date.year
                    try:
                        return datetime(year, i, day)
                    except ValueError:
                        pass
        return None

    def _is_medication_related(self, text: str) -> bool:
        med_markers = [
            r"\b(medication|medicine|drug|pill|tablet|capsule|dose|dosage|mg|mcg|ml|injection|infusion|patch|cream|ointment|suppository)\b",
            r"\b(take|taking|took|start|stop|continue|increase|decrease|hold|switch)\s+\w+\s+(medication|drug|medicine|pill)\b",
        ]
        text_lower = text.lower()
        for marker in med_markers:
            if re.search(marker, text_lower):
                return True
        return False

    def _is_recurring(self, text: str) -> Tuple[bool, Optional[str]]:
        text_lower = text.lower()
        for pattern, recurrence in self.RECURRENCE_PATTERNS:
            if re.search(pattern, text_lower):
                return True, recurrence or "daily"
        return False, None

    def _is_explicit_instruction(self, text: str, modality: str) -> bool:
        if modality in ("required", "prohibited"):
            return True
        if modality == "recommended":
            explicit_markers = [r"\b(please|must|shall|should|need to|have to|important|essential|necessary)\b"]
            text_lower = text.lower()
            for marker in explicit_markers:
                if re.search(marker, text_lower):
                    return True
        return False

    def _requires_confirmation(self, modality: str, is_explicit: bool, confidence: float, is_medication: bool, actor_type: str = "unknown") -> bool:
        if is_medication:
            return True
        if actor_type == "unknown" and modality in ("required", "prohibited"):
            return True
        if not is_explicit and confidence < 0.8:
            return True
        if modality == "optional":
            return True
        if confidence < 0.7:
            return True
        return False

    def _build_normalized_action(self, action_type: str, action_object: Optional[str], modality: str) -> str:
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
        verb = verb_map.get(action_type, "Address")
        obj = action_object or "the specified item"
        modality_prefix = ""
        if modality == "recommended":
            modality_prefix = "(Recommended) "
        elif modality == "optional":
            modality_prefix = "(Optional) "
        elif modality == "prohibited":
            modality_prefix = "(Prohibited) "
        elif modality == "conditional":
            modality_prefix = "(Conditional) "

        return f"{modality_prefix}{verb} {obj}"

    def _build_provenance_note(self, sentence: str, modality: str, action_type: str, is_explicit: bool, confidence: float) -> str:
        parts = [f"Detected modality '{modality}' with confidence {confidence:.2f}."]
        parts.append(f"Action type classified as '{action_type}'.")
        if not is_explicit:
            parts.append("Action is inferred rather than explicitly stated.")
        if confidence < 0.7:
            parts.append("Low extraction confidence; action requires human review.")
        return " ".join(parts)
