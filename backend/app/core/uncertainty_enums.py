from enum import Enum


class EpistemicState(str, Enum):
    KNOWN = "known"
    PARTIALLY_KNOWN = "partially_known"
    AMBIGUOUS = "ambiguous"
    CONFLICTING = "conflicting"
    UNKNOWN = "unknown"
    NOT_DOCUMENTED = "not_documented"
    NOT_ASSESSED = "not_assessed"
    NOT_APPLICABLE = "not_applicable"
    STALE = "stale"
    HISTORICAL = "historical"


class GapReason(str, Enum):
    ABSENT = "absent"
    NOT_YET_OBSERVED = "not_yet_observed"
    NOT_PROVIDED = "not_provided"
    NOT_DOCUMENTED = "not_documented"
    NOT_ASSESSED = "not_assessed"
    AMBIGUOUS = "ambiguous"
    CONFLICTING = "conflicting"
    LOW_QUALITY = "low_quality"
    OUTDATED = "outdated"
    MISSING_CONTEXT = "missing_context"
    EXPECTED_BUT_UNAVAILABLE = "expected_but_unavailable"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"
    INACCESSIBLE = "inaccessible"


class Priority(str, Enum):
    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    INFORMATIONAL = "informational"


class GapLifecycleStatus(str, Enum):
    OPEN = "open"
    PARTIALLY_RESOLVED = "partially_resolved"
    RESOLVED = "resolved"
    REOPENED = "reopened"
    SUPERSEDED = "superseded"
    CLOSED = "closed"


class ResolutionMechanism(str, Enum):
    NEW_EVIDENCE = "new_evidence"
    USER_INPUT = "user_input"
    SOURCE_CONFIRMATION = "source_confirmation"
    INFERENCE = "inference"
    TIME = "time"
    EXPLICIT_CLOSE = "explicit_close"
