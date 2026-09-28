from enum import Enum


class PersonAttributeDomain(str, Enum):
    COMFORT = "comfort"
    PREFERENCE = "preference"
    ROUTINE = "routine"
    RELATIONSHIP = "relationship"
    HISTORY = "history"
    COMMUNICATION = "communication"
    WELLBEING = "wellbeing"
    IDENTITY = "identity"
    OTHER = "other"


class ProfileCompleteness(str, Enum):
    """How much the record actually says about this person.

    Thin is a real and common state early in a care relationship, and it is
    reported rather than hidden. A thin profile is the signal to go and ask.
    """

    THIN = "thin"
    PARTIAL = "partial"
    SUBSTANTIAL = "substantial"


class ContinuityDirection(str, Enum):
    STABLE = "stable"
    IMPROVING = "improving"
    DECLINING = "declining"
    UNCERTAIN = "uncertain"
    INSUFFICIENT_DATA = "insufficient_data"


class HandoffReason(str, Enum):
    CAREGIVER_CHANGE = "caregiver_change"
    SETTING_CHANGE = "setting_change"
    TIME_GAP = "time_gap"
    DISCHARGE = "discharge"
    ESCALATION = "escalation"
    ROUTINE = "routine"


class MattersCategory(str, Enum):
    VALUE = "value"
    GOAL = "goal"
    PREFERENCE = "preference"
    RELATIONSHIP = "relationship"
    DIGNITY = "dignity"
    AUTONOMY = "autonomy"
    COMFORT = "comfort"
    IDENTITY = "identity"
    SPIRITUAL = "spiritual"
    PRACTICAL = "practical"
    CARE_PREFERENCE = "care_preference"


class MattersImportance(str, Enum):
    LOW = "low"
    MODERATE = "moderate"
    HIGH = "high"
    DEFINING = "defining"


class AlignmentVerdict(str, Enum):
    SERVES = "serves"
    PARTLY_SERVES = "partly_serves"
    DOES_NOT_SERVE = "does_not_serve"
    CONFLICTS = "conflicts"
    UNKNOWN = "unknown"


class TrajectoryDirection(str, Enum):
    IMPROVING = "improving"
    STABLE = "stable"
    DECLINING = "declining"
    VOLATILE = "volatile"
    INSUFFICIENT_DATA = "insufficient_data"


class BehaviorCategory(str, Enum):
    AGITATION = "agitation"
    WANDERING = "wandering"
    REFUSAL = "refusal"
    CONFUSION = "confusion"
    MOOD = "mood"
    SLEEP = "sleep"
    APPETITE = "appetite"
    INCONTINENCE = "incontinence"
    PAIN_DISTRESS = "pain_distress"
    REPETITIVE = "repetitive"
    ELOPEMENT = "elopement"
    OTHER = "other"


class HypothesisStrength(str, Enum):
    SUPPORTED = "supported"
    POSSIBLE = "possible"
    UNSUPPORTED = "unsupported"
    CONTRADICTED = "contradicted"


class ContradictionKind(str, Enum):
    SUPERSESSION = "supersession"
    SCOPE_MISMATCH = "scope_mismatch"
    OBSERVER_DIFFERENCE = "observer_difference"
    TERMINOLOGY = "terminology"
    GENUINE_CONFLICT = "genuine_conflict"
    OTHER = "other"


class ContradictionResolutionPath(str, Enum):
    ASK_OBSERVER = "ask_observer"
    ASK_CLINICIAN = "ask_clinician"
    OBSERVE_FURTHER = "observe_further"
    ACCEPT_BOTH = "accept_both"
    CAREGIVER_DECIDES = "caregiver_decides"


class DecisionStatus(str, Enum):
    PROPOSED = "proposed"
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    SUPERSEDED = "superseded"
    EXPIRED = "expired"


class DecisionOwner(str, Enum):
    CAREGIVER = "caregiver"
    CLINICIAN = "clinician"
    SHARED = "shared"
    PERSON = "person"


class PlanStatus(str, Enum):
    ACTIVE = "active"
    PAUSED = "paused"
    COMPLETED = "completed"
    ABANDONED = "abandoned"
    SUPERSEDED = "superseded"


class FollowUpStatus(str, Enum):
    SCHEDULED = "scheduled"
    DUE = "due"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    SUPERSEDED = "superseded"


class FollowUpTrigger(str, Enum):
    OPEN_QUESTION = "open_question"
    UNCERTAINTY = "uncertainty"
    TRAJECTORY = "trajectory"
    SAFETY = "safety"
    CONTRADICTION = "contradiction"
    PLANNED_REVIEW = "planned_review"
    BASELINE = "baseline"


class EngagementTrigger(str, Enum):
    CHANGE_DETECTED = "change_detected"
    FOLLOW_UP_DUE = "follow_up_due"
    CONTRADICTION_OPEN = "contradiction_open"
    GUTTER_QUIET = "gutter_quiet"
    SCHEDULED_REVIEW = "scheduled_review"
    SAFETY = "safety"
    PLAN_REVIEW = "plan_review"


class EngagementUrgency(str, Enum):
    NOW = "now"
    TODAY = "today"
    THIS_WEEK = "this_week"
    NOT_NOW = "not_now"


class ConversationStage(str, Enum):
    IN_CARE_CONTEXT = "in_care_context"
    DRIFTING = "drifting"
    OUT_OF_CARE_CONTEXT = "out_of_care_context"
    RETURNING = "returning"


class SpeakerRole(str, Enum):
    CAREGIVER = "caregiver"
    SOLENOS = "solenos"
    SYSTEM = "system"
