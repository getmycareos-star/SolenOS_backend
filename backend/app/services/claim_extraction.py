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
    # Extraction does not establish truth or calibrated confidence. Leave unknown unless a validated extractor supplies it.
    confidence: Optional[float] = None
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
            claims.append(ClaimCandidate(
                text=sentence.strip(),
                claim_type=claim_type,
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
    """Explainable evidence-quality rules.

    Source authority, temporal relevance, evidence quality, and truth are separate
    concepts. This evaluator never treats source role as a numeric truth score.
    """

    RULE_VERSION = "evidence-quality-rules-v2"

    def assess(
        self,
        authority: SourceAuthority,
        currentness: CurrentnessStatus,
        *,
        source_text: Optional[str] = None,
        source_identity_verified: Optional[bool] = None,
        directness: Optional[str] = None,
        context_present: Optional[bool] = None,
        supporting_material_present: Optional[bool] = None,
        ambiguity_present: Optional[bool] = None,
    ) -> dict:
        criteria = {
            "source_identity_verified": source_identity_verified,
            "source_text_present": bool(source_text and source_text.strip()) if source_text is not None else None,
            "directness": directness,
            "context_present": context_present,
            "supporting_material_present": supporting_material_present,
            "ambiguity_present": ambiguity_present,
        }
        rationale = []
        if authority == SourceAuthority.UNKNOWN or source_identity_verified is not True:
            rationale.append("Source identity or attribution is not verified.")
        if source_text is None or not source_text.strip():
            rationale.append("Original evidence text is missing.")
        if directness is None:
            rationale.append("Directness of the source to the event is unknown.")
        if context_present is not True:
            rationale.append("Relevant context has not been established.")
        if ambiguity_present is True:
            rationale.append("The evidence is explicitly marked ambiguous.")
        if supporting_material_present is False:
            rationale.append("No supporting material was supplied.")
        if currentness == CurrentnessStatus.UNKNOWN_CURRENCY:
            rationale.append("Temporal relevance is unknown; this is not a judgment of truth.")
        elif currentness == CurrentnessStatus.STALE:
            rationale.append("Evidence may be stale for a current-state question.")
        elif currentness == CurrentnessStatus.HISTORICAL:
            rationale.append("Evidence is historical and may remain valid for historical questions.")

        # No role-only promotion: high quality requires observed, explicit criteria.
        if authority == SourceAuthority.UNKNOWN or source_identity_verified is not True:
            quality = SourceQuality.UNVERIFIED
        elif ambiguity_present is True:
            quality = SourceQuality.LOW
        elif source_text is None or not source_text.strip() or directness is None or context_present is not True:
            quality = SourceQuality.UNVERIFIED
        elif directness == "direct" and context_present is True and supporting_material_present is True and ambiguity_present is False:
            quality = SourceQuality.HIGH
        elif directness in {"direct", "reported", "documented"} and context_present is True and ambiguity_present is False:
            quality = SourceQuality.MODERATE
        else:
            quality = SourceQuality.UNVERIFIED

        return {
            "quality": quality.value,
            "rule_version": self.RULE_VERSION,
            "criteria": criteria,
            "rationale": rationale,
            "limitations": [
                "Evidence quality is not truth, authenticity, or source reliability.",
                "Temporal relevance depends on the question being asked.",
                "No calibrated numeric score is produced.",
            ],
        }

    def evaluate(
        self,
        authority: SourceAuthority,
        currentness: CurrentnessStatus,
        is_contradictory: bool = False,
        **evidence_context,
    ) -> SourceQuality:
        # Contradiction is a relationship between items, not evidence-quality grade.
        # Preserve the argument for callers during migration, but do not use it to
        # downgrade evidence or resolve the conflict.
        return SourceQuality(self.assess(authority, currentness, **evidence_context)["quality"])

    def get_currentness_status(
        self,
        document_time: Optional[datetime],
        uploaded_at: Optional[datetime],
        reference_time: Optional[datetime] = None,
        *,
        max_age_days: Optional[int] = None,
        historical_after_days: Optional[int] = None,
    ) -> CurrentnessStatus:
        """Assess freshness only with an explicit evidence-domain policy.

        No universal seven/thirty/365-day expiry is applied. The caller may set
        a freshness window and, separately, the point at which evidence should
        be classified as historical for the question being asked.
        """
        if reference_time is None:
            reference_time = datetime.now(timezone.utc)
        effective_time = document_time or uploaded_at
        if effective_time is None or max_age_days is None or max_age_days < 0:
            return CurrentnessStatus.UNKNOWN_CURRENCY
        if historical_after_days is not None and historical_after_days < max_age_days:
            return CurrentnessStatus.UNKNOWN_CURRENCY
        if effective_time.tzinfo is None:
            effective_time = effective_time.replace(tzinfo=timezone.utc)
        if reference_time.tzinfo is None:
            reference_time = reference_time.replace(tzinfo=timezone.utc)
        age_seconds = (reference_time - effective_time).total_seconds()
        if age_seconds < 0:
            return CurrentnessStatus.UNKNOWN_CURRENCY
        age_days = age_seconds / 86400
        if age_days <= max_age_days:
            return CurrentnessStatus.CURRENT
        if historical_after_days is not None and age_days > historical_after_days:
            return CurrentnessStatus.HISTORICAL
        return CurrentnessStatus.STALE


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
