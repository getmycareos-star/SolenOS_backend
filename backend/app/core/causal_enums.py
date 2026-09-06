from enum import Enum


class EvidenceStrength(str, Enum):
    WEAK = "weak"
    MODERATE = "moderate"
    STRONG = "strong"


class CausalConfidence(str, Enum):
    POSSIBLE = "possible"
    PLAUSIBLE = "plausible"
    SUPPORTED = "supported"
    ESTABLISHED = "established"
    CONFLICTING = "conflicting"
    INSUFFICIENT = "insufficient"
    UNKNOWN = "unknown"


class CausalRelationshipStatus(str, Enum):
    OBSERVED_ASSOCIATION = "observed_association"
    POTENTIAL_RELATIONSHIP = "potential_relationship"
    CAUSAL_HYPOTHESIS = "causal_hypothesis"
    SUPPORTED = "supported"
    CONFLICTING = "conflicting"
    UNRESOLVED = "unresolved"
    CAUSALITY_UNKNOWN = "causality_unknown"


class CausalDirection(str, Enum):
    CAUSE_TO_OUTCOME = "cause_to_outcome"
    OUTCOME_TO_CAUSE = "outcome_to_cause"
    BIDIRECTIONAL = "bidirectional"
    UNKNOWN = "unknown"


class EvidenceRole(str, Enum):
    SUPPORTING = "supporting"
    CONTRADICTORY = "contradictory"
    NEUTRAL = "neutral"


class EvidenceType(str, Enum):
    TEMPORAL_PROXIMITY = "temporal_proximity"
    CLINICIAN_DOCUMENTATION = "clinician_documentation"
    REPRODUCIBLE_RELATIONSHIP = "reproducible_relationship"
    CONTROLLED_OR_INFORMATIVE = "controlled_or_informative"
    ADVERSE_EFFECT_RELATIONSHIP = "adverse_effect_relationship"
    RESOLUTION_ON_REMOVAL = "resolution_on_removal"
    REEVALUATION_CHALLENGE = "reevaluation_challenge"
    CAREGIVER_OBSERVATION = "caregiver_observation"
    CORRELATION = "correlation"
    COINCIDENTAL = "coincidental"
    UNVERIFIED_ATTRIBUTION = "unverified_attribution"
