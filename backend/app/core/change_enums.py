from enum import Enum


class ChangeType(str, Enum):
    ONSET = "onset"
    CESSATION = "cessation"
    INCREASE = "increase"
    DECREASE = "decrease"
    RECURRENCE = "recurrence"
    TRANSITION = "transition"
    PERSISTENCE = "persistence"
    FLUCTUATION = "fluctuation"
    STABILIZATION = "stabilization"
    RECOVERY = "recovery"
    EMERGENCE = "emergence"
    DISAPPEARANCE = "disappearance"


class ChangeDirection(str, Enum):
    INCREASING = "increasing"
    DECREASING = "decreasing"
    STABLE = "stable"
    FLUCTUATING = "fluctuating"
    UNKNOWN = "unknown"


class PatternType(str, Enum):
    RECURRENCE = "recurrence"
    FREQUENCY_INCREASE = "frequency_increase"
    FREQUENCY_DECREASE = "frequency_decrease"
    TREND = "trend"
    ACCELERATION = "acceleration"
    PERSISTENCE = "persistence"
    CLUSTERING = "clustering"
    FLUCTUATION = "fluctuation"
    INSTABILITY = "instability"
    RECOVERY = "recovery"
    DEVIATION = "deviation"
    CONVERGENCE = "convergence"
    DIVERGENCE = "divergence"


class PatternStrength(str, Enum):
    EMERGING = "emerging"
    ESTABLISHED = "established"
    WEAK = "weak"
    NONE = "none"


class SituationState(str, Enum):
    ACTIVE = "active"
    HISTORICAL = "historical"
    RESOLVED = "resolved"
    EVOLVED = "evolved"
    UNKNOWN = "unknown"


class SituationType(str, Enum):
    FUNCTIONAL_INSTABILITY = "functional_instability"
    MEDICATION_INSTABILITY = "medication_instability"
    DECLINE_TRAJECTORY = "decline_trajectory"
    RECOVERY_TRAJECTORY = "recovery_trajectory"
    STABLE_STATE = "stable_state"
    FLUCTUATING_STATE = "fluctuating_state"
    UNKNOWN = "unknown"


class BaselineType(str, Enum):
    STATE = "state"
    PERIOD_FREQUENCY = "period_frequency"
    EXPECTED_FREQUENCY = "expected_frequency"
    PERSONAL_HISTORICAL = "personal_historical"


class BaselineConfidence(str, Enum):
    HIGH = "high"
    MODERATE = "moderate"
    LOW = "low"
    INSUFFICIENT = "insufficient"


class SignificanceVerdict(str, Enum):
    MEANINGFUL_CANDIDATE = "meaningful_candidate"
    NOT_MEANINGFUL = "not_meaningful"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"


class EvidenceStatus(str, Enum):
    CONFIRMED = "confirmed"
    REPORTED = "reported"
    INFERRED = "inferred"
    CONTRADICTORY = "contradictory"
    UNKNOWN = "unknown"
