from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import List, Optional
from dataclasses import dataclass
from app.schemas.safety import EvidenceReference
from app.core.safety_enums import (
    ClaimType,
    ClaimStrength,
    SourceAuthority,
    SourceQuality,
    CurrentnessStatus,
)


@dataclass
class ClaimCandidate:
    text: str
    claim_type: ClaimType
    confidence: float = 1.0
    source_span: Optional[str] = None
    context: Optional[str] = None


class ClaimExtractionEngine:
    CLAIM_PATTERNS = [
        (r"\b(caused by|due to|because of|result(ed)? of|led to|triggered by|caused|cause)\b", ClaimType.CAUSAL),
        (r"\b(diagnosed with|has dementia|dementia progression|progressive disease|consistent with|indicating|dementia is progressing)\b", ClaimType.DIAGNOSTIC),
        (r"\b(take|stop|start|increase|decrease|change|prescribe|should take|must take)\b.*?\b(medication|drug|dose|pill)\b", ClaimType.MEDICAL_ACTION),
        (r"\b(recommend|should|ought to|consider|suggest|advise)\b", ClaimType.RECOMMENDATION),
        (r"\b(will|going to|predict|likely to|expected to)\b", ClaimType.PREDICTION),
        (r"\b(currently|right now|at present|current state|now|today)\b", ClaimType.CURRENT_STATE),
        (r"\b(on|at|in|during|before|after|since|until)\b.*?\b\d{4}|\b(day|week|month|year)\b", ClaimType.TEMPORAL),
        (r"\b(patient|person|mom|dad|resident)\b.*?\b(is|has|was|were|took|started|stopped|increased|decreased)\b", ClaimType.FACTUAL),
    ]

    INTERPRETATION_MARKERS = [
        "suggests", "may indicate", "could indicate", "possibly", "appears to",
        "seems to", "might suggest", "raises the possibility",
    ]

    def extract(self, text: str, source_evidence_id: Optional[str] = None) -> List[ClaimCandidate]:
        claims = []
        sentences = self._split_sentences(text)

        for sentence in sentences:
            if not sentence.strip():
                continue
            claim_type = self._detect_claim_type(sentence)
            confidence = self._estimate_confidence(sentence)
            claims.append(ClaimCandidate(
                text=sentence.strip(),
                claim_type=claim_type,
                confidence=confidence,
                source_span=sentence.strip(),
            ))

        return claims

    def _split_sentences(self, text: str) -> List[str]:
        text = re.sub(r",\s*(and|or)\s+", ", ", text)
        parts = re.split(r"(?<=[.!?])\s+", text)
        return [s.strip() for s in parts if s.strip()]

    def _detect_claim_type(self, text: str) -> ClaimType:
        text_lower = text.lower()

        for pattern, claim_type in self.CLAIM_PATTERNS:
            if re.search(pattern, text_lower):
                return claim_type

        if any(marker in text_lower for marker in self.INTERPRETATION_MARKERS):
            return ClaimType.INTERPRETATION

        return ClaimType.FACTUAL

    def _estimate_confidence(self, text: str) -> float:
        text_lower = text.lower()
        definitive = ["definitely", "certainly", "always", "never", "must", "confirmed"]
        uncertain = ["possibly", "might", "could", "may", "perhaps", "unclear", "unknown"]

        for marker in definitive:
            if marker in text_lower:
                return 0.9
        for marker in uncertain:
            if marker in text_lower:
                return 0.4

        return 0.7


class EvidenceMatcher:
    def match_claim_to_evidence(self, claim_text: str, evidence_refs: List[EvidenceReference]) -> List[EvidenceReference]:
        matched = []
        claim_words = set(claim_text.lower().split())

        for ev in evidence_refs:
            if not ev.source_text:
                continue
            evidence_words = set(ev.source_text.lower().split())
            overlap = claim_words & evidence_words
            if len(overlap) >= max(1, len(claim_words) * 0.25):
                matched.append(ev)

        return matched

    def find_contradictions(self, claim_text: str, evidence_refs: List[EvidenceReference]) -> List[EvidenceReference]:
        contradictions = []
        negation_patterns = [
            r"\bno\b", r"\bnot\b", r"\bnever\b", r"\bwithout\b", r"\bnegative\b",
            r"\babsent\b", r"\bdenies\b", r"\bnone\b", r"\bfailed\b",
            r"\bdiscontinued\b", r"\bineffective\b", r"\bstopp?ed\b",
        ]

        for ev in evidence_refs:
            if not ev.source_text:
                continue
            text_lower = ev.source_text.lower()
            for pattern in negation_patterns:
                if re.search(pattern, text_lower):
                    if self._texts_related(claim_text, ev.source_text):
                        contradictions.append(ev)
                        break

        return contradictions

    def _texts_related(self, text_a: str, text_b: str) -> bool:
        words_a = set(text_a.lower().split())
        words_b = set(text_b.lower().split())
        overlap = words_a & words_b
        if len(overlap) >= 1:
            return True
        key_words_a = {w for w in words_a if len(w) > 3}
        key_words_b = {w for w in words_b if len(w) > 3}
        overlap_key = key_words_a & key_words_b
        return len(overlap_key) >= 1


class SourceQualityEvaluator:
    AUTHORITY_SCORES = {
        SourceAuthority.CLINICIAN: 0.95,
        SourceAuthority.LABORATORY: 0.95,
        SourceAuthority.MEDICATION_RECORD: 0.9,
        SourceAuthority.DISCHARGE_INSTRUCTION: 0.9,
        SourceAuthority.EXTERNAL_GUIDELINE: 0.85,
        SourceAuthority.CAREGIVER_REPORT: 0.7,
        SourceAuthority.PATIENT_REPORT: 0.7,
        SourceAuthority.DOCUMENT: 0.6,
        SourceAuthority.SYSTEM_INFERENCE: 0.4,
        SourceAuthority.UNKNOWN: 0.3,
    }

    QUALITY_THRESHOLDS = {
        SourceQuality.HIGH: 0.8,
        SourceQuality.MODERATE: 0.5,
        SourceQuality.LOW: 0.3,
        SourceQuality.UNVERIFIED: 0.0,
    }

    def evaluate(self, authority: SourceAuthority, currentness: CurrentnessStatus, is_contradictory: bool = False) -> SourceQuality:
        if is_contradictory:
            return SourceQuality.LOW

        base_score = self.AUTHORITY_SCORES.get(authority, 0.3)

        if currentness == CurrentnessStatus.STALE:
            base_score *= 0.6
        elif currentness == CurrentnessStatus.HISTORICAL:
            base_score *= 0.3
        elif currentness == CurrentnessStatus.UNKNOWN_CURRENCY:
            base_score *= 0.5

        for quality, threshold in sorted(self.QUALITY_THRESHOLDS.items(), key=lambda x: x[1], reverse=True):
            if base_score >= threshold:
                return quality

        return SourceQuality.UNVERIFIED

    def get_currentness_status(self, document_time: Optional[datetime], uploaded_at: Optional[datetime], reference_time: Optional[datetime] = None) -> CurrentnessStatus:
        if reference_time is None:
            reference_time = datetime.now(timezone.utc)

        if document_time is None and uploaded_at is None:
            return CurrentnessStatus.UNKNOWN_CURRENCY

        effective_time = document_time or uploaded_at
        age_days = (reference_time - effective_time).days

        if age_days <= 7:
            return CurrentnessStatus.CURRENT
        elif age_days <= 30:
            return CurrentnessStatus.RECENT
        elif age_days <= 365:
            return CurrentnessStatus.STALE
        else:
            return CurrentnessStatus.HISTORICAL


class ClaimStrengthMatcher:
    STRENGTH_ORDER = {
        ClaimStrength.NOT_DOCUMENTED: 0,
        ClaimStrength.UNKNOWN: 1,
        ClaimStrength.HISTORICAL: 2,
        ClaimStrength.STALE: 3,
        ClaimStrength.CONFLICTING: 4,
        ClaimStrength.UNCERTAIN: 5,
        ClaimStrength.POSSIBLE: 6,
        ClaimStrength.LIKELY: 7,
        ClaimStrength.DEFINITE: 8,
    }

    def matches(self, claim_strength: ClaimStrength, evidence_strength: ClaimStrength) -> bool:
        claim_level = self.STRENGTH_ORDER.get(claim_strength, 0)
        evidence_level = self.STRENGTH_ORDER.get(evidence_strength, 0)
        return claim_level <= evidence_level + 1

    def mismatch_description(self, claim_strength: ClaimStrength, evidence_strength: ClaimStrength) -> str:
        return (
            f"Claim strength ({claim_strength.value}) exceeds evidence strength ({evidence_strength.value}). "
            f"Wording should be reduced to match the available evidence."
        )
