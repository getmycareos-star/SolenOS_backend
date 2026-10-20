"""Evidence Extraction Service - Converts raw information into structured evidence.

Implements:
- Evidence extraction from text/documents (Requirement 4)
- Evidence typing (Requirement 5, 6)
- Subject/observer/author distinction (Requirements 7, 8, 9)
- Normalization preserving original expression (Requirements 11, 12)
- Temporal resolution with granularity (Requirements 13, 14, 15)
- Negation/uncertainty/hedging preservation (Requirements 47, 48, 49)
- Entity resolution separate from extraction (Requirement 10)
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple
from dataclasses import dataclass, field
from enum import Enum

from app.core.evidence_enums import (
    EvidenceType,
    EvidenceSubjectRole,
    EvidenceObserverRole,
    EvidenceAuthorRole,
    SourceType,
    NormalizationType,
    ExtractionMethod,
    NegationStatus,
    UncertaintyLevel,
    HedgeType,
    EntityResolutionStatus,
    TemporalGranularity,
    TemporalAnchorType,
)
from app.schemas.evidence import (
    SourceLocation,
    ConfidenceDimensions,
    TemporalInfo,
    NormalizationInfo,
    ExtractionMetadata,
    QualityGateResult,
)
from app.services.source_pointer import verify_source_pointer


@dataclass
class ExtractedEvidence:
    """Intermediate representation during extraction."""
    type: EvidenceType
    subtype: Optional[str] = None
    source_text: str = ""
    subject_role: EvidenceSubjectRole = EvidenceSubjectRole.CARE_RECIPIENT
    subject_id: Optional[str] = None
    subject_identifier: Optional[str] = None
    entity_resolution_status: EntityResolutionStatus = EntityResolutionStatus.UNRESOLVED
    entity_resolution_confidence: Optional[float] = None
    observer_role: Optional[EvidenceObserverRole] = None
    observer_id: Optional[str] = None
    observer_name: Optional[str] = None
    author_role: Optional[EvidenceAuthorRole] = None
    author_id: Optional[str] = None
    author_name: Optional[str] = None
    negation_status: NegationStatus = NegationStatus.ASSERTED
    uncertainty_level: UncertaintyLevel = UncertaintyLevel.CERTAIN
    hedge_type: Optional[HedgeType] = None
    hedge_text: Optional[str] = None
    temporal: Optional[TemporalInfo] = None
    normalization: Optional[NormalizationInfo] = None
    source_location: Optional[SourceLocation] = None
    extraction: Optional[ExtractionMetadata] = None
    confidence: Optional[ConfidenceDimensions] = None
    metadata: Dict[str, Any] = field(default_factory=dict)


class EvidenceExtractionEngine:
    """Extract structured evidence from raw text/documents."""

    # Patterns for evidence type detection (Requirement 5, 6)
    TYPE_PATTERNS = {
        EvidenceType.BLOOD_PRESSURE: [
            r"\b(?:bp|blood pressure)\s*(?:was|is|:)?\s*\d+\s*/\s*\d+\b",
            r"\b\d+\s*/\s*\d+\s*mmhg\b",
        ],
        EvidenceType.WEIGHT: [
            r"\b(?:weight|weighs|weighed)\s*(?:was|is|:)?\s*\d+(?:\.\d+)?\s*(?:kg|lbs|pounds)\b",
        ],
        EvidenceType.TEMPERATURE: [
            r"\b(?:temp|temperature)\s*(?:was|is|:)?\s*\d+(?:\.\d+)?\s*(?:c|f|celsius|fahrenheit)\b",
            r"\b\d+(?:\.\d+)?\s*(?:°?[cf])\b",
        ],
        EvidenceType.GLUCOSE: [
            r"\b(?:glucose|blood sugar|sugar)\s*(?:was|is|:)?\s*\d+(?:\.\d+)?\s*(?:mg/dl|mmol/l)\b",
        ],
        EvidenceType.MEDICATION_MENTIONED: [
            r"\b(?:take|taking|took|started|stopped|prescribed)\s+\w+",
            r"\b(?:medication|medicine|drug|pill)\s+\w+",
        ],
        EvidenceType.MEDICATION_TAKEN: [
            r"\b(?:took|taken|gave|given|administered)\s+\w+",
        ],
        EvidenceType.MEDICATION_MISSED: [
            r"\b(?:missed|forgot|didn't take|did not take)\s+\w+",
        ],
        EvidenceType.MEDICATION_REFUSED: [
            r"\b(?:refused|refuses|won't take|will not take)\s+\w+",
        ],
        EvidenceType.OBSERVATION: [
            r"\b(?:fell|fall|fallen)\b",
        ],
        EvidenceType.COGNITIVE_OBSERVATION: [
            r"\b(?:confused|confusion|memory|forgot|disoriented|dementia)\b",
        ],
        EvidenceType.MOBILITY_OBSERVATION: [
            r"\b(?:walk|walking|mobility|balance|unsteady|gait)\b",
        ],
        EvidenceType.SLEEP_OBSERVATION: [
            r"\b(?:sleep|slept|insomnia|awake|woke up)\b",
        ],
        EvidenceType.APPETITE_OBSERVATION: [
            r"\b(?:appetite|eat|eating|hungry|nausea|vomiting)\b",
        ],
        EvidenceType.APPOINTMENT_EVENT: [
            r"\b(?:appointment|visit|see|saw)\s+(?:doctor|dr\.|physician|nurse|specialist)\b",
        ],
        EvidenceType.SYMPTOM_REPORT: [
            r"\b(?:pain|ache|hurt|sore|symptom|feeling)\b",
        ],
        EvidenceType.CAREGIVER_COMMUNICATION: [
            r"\b(?:said|told|mentioned|reported|called|texted|emailed)\b",
        ],
    }

    # Negation patterns (Requirement 47)
    NEGATION_PATTERNS = [
        r"\bno\b", r"\bnot\b", r"\bnever\b", r"\bwithout\b", r"\bnegative\b",
        r"\babsent\b", r"\bdenies?\b", r"\bdenied\b", r"\bnone\b", r"\bfailed\b",
        r"\bdiscontinued\b", r"\bineffective\b", r"\bstopped?\b",
        r"\bno longer\b", r"\bno more\b",
    ]

    # Uncertainty patterns (Requirement 48)
    UNCERTAINTY_PATTERNS = [
        r"\bmaybe\b", r"\bpossibly\b", r"\bprobably\b", r"\bappears\b",
        r"\bseems\b", r"\bmight\b", r"\bcould\b", r"\bmay\b",
        r"\buncertain\b", r"\bunclear\b", r"\bunknown\b",
        r"\bi think\b", r"\bi believe\b", r"\bguess\b",
    ]

    # Hedge patterns (Requirement 49)
    HEDGE_PATTERNS = {
        HedgeType.APPEARS: [r"\bappears\b"],
        HedgeType.POSSIBLY: [r"\bpossibly\b"],
        HedgeType.MIGHT: [r"\bmight\b"],
        HedgeType.SEEMS: [r"\bseems\b"],
        HedgeType.I_THINK: [r"\bi think\b"],
        HedgeType.NOT_SURE: [r"\bnot sure\b", r"\bunsure\b"],
        HedgeType.MAY: [r"\bmay\b"],
        HedgeType.COULD: [r"\bcould\b"],
        HedgeType.SUGGESTS: [r"\bsuggests\b"],
        HedgeType.INDICATES: [r"\bindicates\b"],
    }

    # Temporal patterns (Requirements 13, 14, 15)
    TEMPORAL_PATTERNS = {
        TemporalGranularity.EXACT_TIMESTAMP: [
            r"\d{4}-\d{2}-\d{2}[T\s]\d{2}:\d{2}:\d{2}",
            r"\d{2}/\d{2}/\d{4}\s+\d{2}:\d{2}",
        ],
        TemporalGranularity.DATE: [
            r"\d{4}-\d{2}-\d{2}",
            r"\d{2}/\d{2}/\d{4}",
            r"\b(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\s+\d{1,2},?\s+\d{4}\b",
        ],
        TemporalGranularity.MONTH_YEAR: [
            r"\b(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\s+\d{4}\b",
        ],
        TemporalGranularity.RELATIVE: [
            r"\byesterday\b", r"\btoday\b", r"\btomorrow\b",
            r"\blast\s+(?:night|week|month|year)\b",
            r"\bthis\s+(?:morning|afternoon|evening|week|month)\b",
            r"\bnext\s+(?:week|month|year)\b",
            r"\b\d+\s+(?:days?|weeks?|months?|years?)\s+ago\b",
            r"\bsince\s+\w+\b", r"\buntil\s+\w+\b",
        ],
        TemporalGranularity.APPROXIMATE: [
            r"\babout\s+\w+\b", r"\bapproximately\s+\w+\b",
            r"\baround\s+\w+\b", r"\brecently\b",
            r"\b\w+\s+ago\b",
        ],
        TemporalGranularity.TIME_RANGE: [
            r"\bbetween\s+\w+\s+and\s+\w+\b",
            r"\bfrom\s+\w+\s+to\s+\w+\b",
        ],
    }

    def __init__(self) -> None:
        self.compiled_type_patterns = {
            etype: [re.compile(p, re.IGNORECASE) for p in patterns]
            for etype, patterns in self.TYPE_PATTERNS.items()
        }
        self.compiled_negation = [re.compile(p, re.IGNORECASE) for p in self.NEGATION_PATTERNS]
        self.compiled_uncertainty = [re.compile(p, re.IGNORECASE) for p in self.UNCERTAINTY_PATTERNS]
        self.compiled_hedges = {
            htype: [re.compile(p, re.IGNORECASE) for p in patterns]
            for htype, patterns in self.HEDGE_PATTERNS.items()
        }
        self.compiled_temporal = {
            gran: [re.compile(p, re.IGNORECASE) for p in patterns]
            for gran, patterns in self.TEMPORAL_PATTERNS.items()
        }

    def extract_from_text(
        self,
        text: str,
        source_id: Optional[str] = None,
        source_type: SourceType = SourceType.FREE_FORM_NOTE,
        caregiver_id: Optional[str] = None,
        person_id: Optional[str] = None,
    ) -> List[ExtractedEvidence]:
        """Extract evidence from raw text. Requirement 4."""
        if not text or not text.strip():
            return []

        # Split into sentences/segments for granular extraction
        segments = self._split_into_segments(text)
        evidence_list = []

        for i, segment in enumerate(segments):
            if not segment.strip():
                continue

            extracted = self._extract_from_segment(
                segment=segment,
                segment_index=i,
                full_text=text,
                source_id=source_id,
                source_type=source_type,
                caregiver_id=caregiver_id,
                person_id=person_id,
            )
            if extracted:
                evidence_list.append(extracted)

        return evidence_list

    def _split_into_segments(self, text: str) -> List[str]:
        """Split text into meaningful segments for extraction."""
        # Simple sentence splitting - could be enhanced with NLP
        sentences = re.split(r"(?<=[.!?])\s+", text)
        return [s.strip() for s in sentences if s.strip()]

    def _extract_from_segment(
        self,
        segment: str,
        segment_index: int,
        full_text: str,
        source_id: Optional[str],
        source_type: SourceType,
        caregiver_id: Optional[str],
        person_id: Optional[str],
    ) -> Optional[ExtractedEvidence]:
        """Extract evidence from a single text segment."""
        # Determine evidence type
        evidence_type = self._detect_evidence_type(segment)
        if evidence_type == EvidenceType.OTHER_SUPPORTED_EVIDENCE and not self._has_meaningful_content(segment):
            return None

        # Detect negation
        negation_status, negation_matches = self._detect_negation(segment)

        # Detect uncertainty
        uncertainty_level = self._detect_uncertainty(segment)

        # Detect hedging
        hedge_type, hedge_text = self._detect_hedging(segment)

        # Extract temporal info
        temporal = self._extract_temporal(segment, full_text)

        # Extract subject/observer/author
        subject_info = self._extract_subject_info(segment, person_id, caregiver_id)
        observer_info = self._extract_observer_info(segment, caregiver_id)
        author_info = self._extract_author_info(segment, caregiver_id)

        # Normalize measurements
        normalization = self._normalize_measurements(segment, evidence_type)

        # Source location
        source_location = self._find_source_location(segment, full_text)

        # Build extraction metadata
        extraction = ExtractionMetadata(
            extraction_method=ExtractionMethod.REGEX_EXTRACTION,
            extraction_version="1.0",
            extraction_model="regex",
            extraction_confidence=self._calculate_extraction_confidence(segment, evidence_type),
            extraction_timestamp=datetime.now(timezone.utc),
        )

        # Confidence dimensions
        confidence = self._calculate_confidence_dimensions(
            segment, evidence_type, negation_status, uncertainty_level, temporal
        )

        return ExtractedEvidence(
            type=evidence_type,
            source_text=segment,
            subject_role=subject_info["role"],
            subject_id=subject_info["id"],
            subject_identifier=subject_info["identifier"],
            entity_resolution_status=subject_info["resolution_status"],
            entity_resolution_confidence=subject_info["resolution_confidence"],
            observer_role=observer_info["role"],
            observer_id=observer_info["id"],
            observer_name=observer_info["name"],
            author_role=author_info["role"],
            author_id=author_info["id"],
            author_name=author_info["name"],
            negation_status=negation_status,
            uncertainty_level=uncertainty_level,
            hedge_type=hedge_type,
            hedge_text=hedge_text,
            temporal=temporal,
            normalization=normalization,
            source_location=source_location,
            extraction=extraction,
            confidence=confidence,
            metadata={"segment_index": segment_index, "source_type": source_type.value},
        )

    def _detect_evidence_type(self, text: str) -> EvidenceType:
        """Detect evidence type from text content. Requirement 5, 6."""
        text_lower = text.lower()

        # Check specific patterns first (more specific = higher priority)
        for etype, patterns in self.compiled_type_patterns.items():
            for pattern in patterns:
                if pattern.search(text_lower):
                    return etype

        # Fallback: keyword-based
        if any(w in text_lower for w in ["bp", "blood pressure", "systolic", "diastolic"]):
            return EvidenceType.BLOOD_PRESSURE
        if any(w in text_lower for w in ["weight", "weigh", "kg", "lbs", "pounds"]):
            return EvidenceType.WEIGHT
        if any(w in text_lower for w in ["temp", "temperature", "fever", "°c", "°f"]):
            return EvidenceType.TEMPERATURE
        if any(w in text_lower for w in ["glucose", "blood sugar", "sugar level"]):
            return EvidenceType.GLUCOSE
        if any(w in text_lower for w in ["medication", "medicine", "drug", "pill", "dose", "prescribed"]):
            return EvidenceType.MEDICATION_MENTIONED
        if any(w in text_lower for w in ["fell", "fall", "fallen"]):
            return EvidenceType.OBSERVATION
        if any(w in text_lower for w in ["confused", "confusion", "memory", "dementia", "disoriented"]):
            return EvidenceType.COGNITIVE_OBSERVATION
        if any(w in text_lower for w in ["walk", "walking", "mobility", "balance", "unsteady"]):
            return EvidenceType.MOBILITY_OBSERVATION
        if any(w in text_lower for w in ["sleep", "slept", "insomnia", "awake"]):
            return EvidenceType.SLEEP_OBSERVATION
        if any(w in text_lower for w in ["appetite", "eat", "eating", "hungry", "nausea", "vomiting"]):
            return EvidenceType.APPETITE_OBSERVATION
        if any(w in text_lower for w in ["appointment", "doctor", "dr.", "visit", "specialist"]):
            return EvidenceType.APPOINTMENT_EVENT
        if any(w in text_lower for w in ["pain", "ache", "hurt", "sore", "symptom"]):
            return EvidenceType.SYMPTOM_REPORT
        if any(w in text_lower for w in ["said", "told", "mentioned", "reported", "called"]):
            return EvidenceType.CAREGIVER_COMMUNICATION

        return EvidenceType.OBSERVATION

    def _has_meaningful_content(self, text: str) -> bool:
        """Check if text has meaningful content beyond stopwords."""
        words = text.lower().split()
        meaningful = [w for w in words if len(w) > 3]
        return len(meaningful) >= 2

    def _detect_negation(self, text: str) -> Tuple[NegationStatus, List[str]]:
        """Detect negation in text. Requirement 47."""
        matches = []
        for pattern in self.compiled_negation:
            for match in pattern.finditer(text):
                matches.append(match.group())

        if matches:
            # Check if it's a quoted/second-hand negation
            if re.search(r'"[^"]*"', text) or re.search(r"'[^']*'", text):
                return NegationStatus.QUOTED, matches
            if any(w in text.lower() for w in ["he said", "she said", "they said", "reported that", "mentioned that"]):
                return NegationStatus.SECOND_HAND, matches
            return NegationStatus.NEGATED, matches
        return NegationStatus.ASSERTED, []

    def _detect_uncertainty(self, text: str) -> UncertaintyLevel:
        """Detect uncertainty level. Requirement 48."""
        text_lower = text.lower()
        for pattern in self.compiled_uncertainty:
            if pattern.search(text_lower):
                return UncertaintyLevel.EXPLICITLY_UNCERTAIN
        return UncertaintyLevel.CERTAIN

    def _detect_hedging(self, text: str) -> Tuple[Optional[HedgeType], Optional[str]]:
        """Detect hedging language. Requirement 49."""
        text_lower = text.lower()
        for htype, patterns in self.compiled_hedges.items():
            for pattern in patterns:
                match = pattern.search(text_lower)
                if match:
                    return htype, match.group()
        return None, None

    def _extract_temporal(self, segment: str, full_text: str) -> Optional[TemporalInfo]:
        """Extract temporal information with granularity. Requirements 13, 14, 15."""
        text_lower = segment.lower()

        for granularity, patterns in self.compiled_temporal.items():
            for pattern in patterns:
                match = pattern.search(text_lower)
                if match:
                    temporal_text = match.group()
                    return TemporalInfo(
                        event_time_text=temporal_text,
                        event_time_granularity=granularity,
                        event_time_anchor=TemporalAnchorType.RELATIVE_EXPRESSION,
                        event_time_anchor_reference="source_document_timestamp",
                    )

        return None

    def _extract_subject_info(
        self, text: str, person_id: Optional[str], caregiver_id: Optional[str]
    ) -> Dict[str, Any]:
        """Extract subject information. Requirement 7."""
        text_lower = text.lower()

        # Check for caregiver as subject
        if any(w in text_lower for w in ["i'm", "i am", "i feel", "i think", "my ", "our ", "me "]):
            return {
                "role": EvidenceSubjectRole.CAREGIVER,
                "id": caregiver_id,
                "identifier": None,
                "resolution_status": EntityResolutionStatus.RESOLVED if caregiver_id else EntityResolutionStatus.UNRESOLVED,
                "resolution_confidence": 1.0 if caregiver_id else None,
            }

        # Check for provider as subject
        if any(w in text_lower for w in ["dr.", "doctor", "physician", "nurse", "pharmacist"]):
            return {
                "role": EvidenceSubjectRole.PROVIDER,
                "id": None,
                "identifier": None,
                "resolution_status": EntityResolutionStatus.UNRESOLVED,
                "resolution_confidence": None,
            }

        # Default: care recipient
        return {
            "role": EvidenceSubjectRole.CARE_RECIPIENT,
            "id": person_id,
            "identifier": None,
            "resolution_status": EntityResolutionStatus.RESOLVED if person_id else EntityResolutionStatus.UNRESOLVED,
            "resolution_confidence": 1.0 if person_id else None,
        }

    def _extract_observer_info(self, text: str, caregiver_id: Optional[str]) -> Dict[str, Any]:
        """Extract observer information. Requirement 8."""
        text_lower = text.lower()

        # Direct observation markers
        if any(w in text_lower for w in ["i saw", "i observed", "i noticed", "i watched", "i witnessed", "saw the"]):
            return {
                "role": EvidenceObserverRole.CAREGIVER,
                "id": caregiver_id,
                "name": None,
            }

        # Second-hand markers
        if any(w in text_lower for w in ["said", "reported", "mentioned", "told", "stated", "according to", "heard that"]):
            # Try to extract who reported
            for role in [EvidenceObserverRole.FAMILY_MEMBER, EvidenceObserverRole.FRIEND, EvidenceObserverRole.NURSE, EvidenceObserverRole.PHYSICIAN]:
                if role.value.replace("_", " ") in text_lower:
                    return {"role": role, "id": None, "name": role.value}

            return {
                "role": EvidenceObserverRole.CAREGIVER,
                "id": caregiver_id,
                "name": None,
            }

        # Provider observation
        if any(w in text_lower for w in ["dr.", "doctor", "physician", "nurse", "clinician", "assessed", "examined"]):
            return {
                "role": EvidenceObserverRole.PROVIDER,
                "id": None,
                "name": None,
            }

        # Default: caregiver
        return {
            "role": EvidenceObserverRole.CAREGIVER,
            "id": caregiver_id,
            "name": None,
        }

    def _extract_author_info(self, text: str, caregiver_id: Optional[str]) -> Dict[str, Any]:
        """Extract author information. Requirement 9."""
        # For manual entry, author = caregiver
        return {
            "role": EvidenceAuthorRole.CAREGIVER,
            "id": caregiver_id,
            "name": None,
        }

    def _normalize_measurements(self, text: str, evidence_type: EvidenceType) -> Optional[NormalizationInfo]:
        """Normalize measurements while preserving original. Requirements 11, 12."""
        original = text

        # Blood pressure normalization
        if evidence_type == EvidenceType.BLOOD_PRESSURE:
            bp_match = re.search(r"(\d+)\s*/\s*(\d+)", text)
            if bp_match:
                systolic, diastolic = int(bp_match.group(1)), int(bp_match.group(2))
                return NormalizationInfo(
                    normalized_text=f"BP {systolic}/{diastolic}",
                    normalization_type=NormalizationType.MEASUREMENT,
                    normalization_details={
                        "measurement_type": "blood_pressure",
                        "systolic": systolic,
                        "diastolic": diastolic,
                        "unit": "mmHg",
                    },
                    original_expression=original,
                )

        # Weight normalization
        if evidence_type == EvidenceType.WEIGHT:
            weight_match = re.search(r"(\d+(?:\.\d+)?)\s*(kg|lbs?|pounds?)", text, re.IGNORECASE)
            if weight_match:
                value, unit = float(weight_match.group(1)), weight_match.group(2).lower()
                normalized_unit = "kg" if unit in ["kg", "kilogram"] else "lbs"
                normalized_value = value if normalized_unit == "kg" else round(value * 0.453592, 1)
                return NormalizationInfo(
                    normalized_text=f"Weight {normalized_value} {normalized_unit}",
                    normalization_type=NormalizationType.MEASUREMENT,
                    normalization_details={
                        "measurement_type": "weight",
                        "value": normalized_value,
                        "unit": normalized_unit,
                        "original_value": value,
                        "original_unit": unit,
                    },
                    original_expression=original,
                )

        # Temperature normalization
        if evidence_type == EvidenceType.TEMPERATURE:
            temp_match = re.search(r"(\d+(?:\.\d+)?)\s*(°?[cf])", text, re.IGNORECASE)
            if temp_match:
                value, unit = float(temp_match.group(1)), temp_match.group(2).lower()
                if unit in ["f", "°f"]:
                    celsius = round((value - 32) * 5/9, 1)
                    normalized_value, normalized_unit = celsius, "°C"
                else:
                    normalized_value, normalized_unit = value, "°C"
                return NormalizationInfo(
                    normalized_text=f"Temperature {normalized_value} {normalized_unit}",
                    normalization_type=NormalizationType.MEASUREMENT,
                    normalization_details={
                        "measurement_type": "temperature",
                        "value": normalized_value,
                        "unit": normalized_unit,
                        "original_value": value,
                        "original_unit": unit,
                    },
                    original_expression=original,
                )

        # Glucose normalization
        if evidence_type == EvidenceType.GLUCOSE:
            glucose_match = re.search(r"(\d+(?:\.\d+)?)\s*(mg/dl|mmol/l)", text, re.IGNORECASE)
            if glucose_match:
                value, unit = float(glucose_match.group(1)), glucose_match.group(2).lower()
                return NormalizationInfo(
                    normalized_text=f"Glucose {value} {unit}",
                    normalization_type=NormalizationType.MEASUREMENT,
                    normalization_details={
                        "measurement_type": "glucose",
                        "value": value,
                        "unit": unit,
                    },
                    original_expression=original,
                )

        # Medication normalization (basic)
        if evidence_type in [EvidenceType.MEDICATION_MENTIONED, EvidenceType.MEDICATION_TAKEN,
                             EvidenceType.MEDICATION_MISSED, EvidenceType.MEDICATION_REFUSED]:
            med_match = re.search(r"\b(take|taking|took|started|stopped|prescribed|gave|given|administered|missed|forgot|refused)\s+(\w+(?:\s+\w+)?)\b", text, re.IGNORECASE)
            if med_match:
                action, med_name = med_match.group(1).lower(), med_match.group(2).strip()
                return NormalizationInfo(
                    normalized_text=f"Medication: {med_name} - {action}",
                    normalization_type=NormalizationType.MEDICATION_NAME,
                    normalization_details={
                        "medication_name": med_name,
                        "action": action,
                    },
                    original_expression=original,
                )

        return None

    def _find_source_location(self, segment: str, full_text: str) -> Optional[SourceLocation]:
        """Find character offsets of segment in full text. Requirement 21, 22."""
        start = full_text.find(segment)
        if start >= 0:
            return SourceLocation(
                text_span_start=start,
                text_span_end=start + len(segment),
            )
        return None

    def _calculate_extraction_confidence(self, text: str, evidence_type: EvidenceType) -> float:
        """Calculate extraction confidence based on pattern match quality."""
        base_confidence = 0.7
        if evidence_type != EvidenceType.OBSERVATION and evidence_type != EvidenceType.OTHER_SUPPORTED_EVIDENCE:
            base_confidence = 0.85
        # Reduce for very short segments
        if len(text.split()) < 3:
            base_confidence *= 0.8
        return round(base_confidence, 2)

    def _calculate_confidence_dimensions(
        self,
        text: str,
        evidence_type: EvidenceType,
        negation_status: NegationStatus,
        uncertainty_level: UncertaintyLevel,
        temporal: Optional[TemporalInfo],
    ) -> ConfidenceDimensions:
        """Calculate multi-dimensional confidence. Requirements 16, 17."""
        extraction_conf = self._calculate_extraction_confidence(text, evidence_type)

        # Entity resolution confidence - depends on whether subject is identified
        entity_conf = 0.9 if "i " not in text.lower() else 0.6

        # Temporal confidence
        temporal_conf = 0.5
        if temporal and temporal.event_time_granularity in [TemporalGranularity.EXACT_TIMESTAMP, TemporalGranularity.DATE]:
            temporal_conf = 0.9
        elif temporal and temporal.event_time_granularity == TemporalGranularity.RELATIVE:
            temporal_conf = 0.6
        elif temporal:
            temporal_conf = 0.4

        # Source support - based on negation/uncertainty
        source_support = 1.0
        if negation_status == NegationStatus.NEGATED:
            source_support = 0.3
        elif uncertainty_level == UncertaintyLevel.EXPLICITLY_UNCERTAIN:
            source_support = 0.5

        # Interpretation confidence - lower for raw observations
        interpretation_conf = 0.6

        return ConfidenceDimensions(
            extraction=extraction_conf,
            entity_resolution=entity_conf,
            temporal=temporal_conf,
            source_support=source_support,
            interpretation=interpretation_conf,
            overall=round((extraction_conf + entity_conf + temporal_conf + source_support + interpretation_conf) / 5, 2),
        )


class EntityResolutionService:
    """Separate entity resolution from extraction. Requirement 10."""

    def __init__(self, db: Session):
        self.db = db

    def resolve_subject(self, evidence: ExtractedEvidence, person_id: Optional[str] = None) -> ExtractedEvidence:
        """Resolve subject entity. Separate from extraction."""
        if evidence.subject_role == EvidenceSubjectRole.CARE_RECIPIENT and person_id:
            person = self.db.query(Person).filter(Person.id == person_id).first()
            if person:
                evidence.subject_id = person_id
                evidence.entity_resolution_status = EntityResolutionStatus.RESOLVED
                evidence.entity_resolution_confidence = 1.0
                return evidence

        # Try to resolve from text
        # This would use NLP/entity linking in production
        return evidence

    def resolve_observer(self, evidence: ExtractedEvidence, caregiver_id: Optional[str] = None) -> ExtractedEvidence:
        """Resolve observer entity."""
        if evidence.observer_role == EvidenceObserverRole.CAREGIVER and caregiver_id:
            caregiver = self.db.query(Caregiver).filter(Caregiver.id == caregiver_id).first()
            if caregiver:
                evidence.observer_id = caregiver_id
                return evidence
        return evidence

    def resolve_author(self, evidence: ExtractedEvidence, caregiver_id: Optional[str] = None) -> ExtractedEvidence:
        """Resolve author entity."""
        if evidence.author_role == EvidenceAuthorRole.CAREGIVER and caregiver_id:
            caregiver = self.db.query(Caregiver).filter(Caregiver.id == caregiver_id).first()
            if caregiver:
                evidence.author_id = caregiver_id
                return evidence
        return evidence


class EvidenceNormalizationService:
    """Normalization preserving original expression. Requirements 11, 12."""

    @staticmethod
    def normalize_measurement(value: float, unit: str, measurement_type: str) -> NormalizationInfo:
        """Normalize a measurement with unit conversion tracking."""
        original = f"{value} {unit}"

        conversions = {
            "weight": {"kg": 1.0, "lbs": 0.453592, "pounds": 0.453592},
            "temperature": {"c": 1.0, "f": 0.5556},  # (F-32)*5/9
            "height": {"cm": 1.0, "in": 2.54, "ft": 30.48},
        }

        if measurement_type in conversions and unit.lower() in conversions[measurement_type]:
            factor = conversions[measurement_type][unit.lower()]
            normalized_value = round(value * factor, 1)
            normalized_unit = "kg" if measurement_type == "weight" else ("°C" if measurement_type == "temperature" else "cm")
        else:
            normalized_value, normalized_unit = value, unit

        return NormalizationInfo(
            normalized_text=f"{measurement_type.capitalize()}: {normalized_value} {normalized_unit}",
            normalization_type=NormalizationType.MEASUREMENT,
            normalization_details={
                "measurement_type": measurement_type,
                "value": normalized_value,
                "unit": normalized_unit,
                "original_value": value,
                "original_unit": unit,
                "conversion_factor": factor if measurement_type in conversions and unit.lower() in conversions[measurement_type] else 1.0,
            },
            original_expression=original,
        )


# Import at bottom to avoid circular imports
from app.models.care import Person, Caregiver
from sqlalchemy.orm import Session