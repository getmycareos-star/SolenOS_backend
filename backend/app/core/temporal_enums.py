from enum import Enum


class TemporalPrecision(str, Enum):
    EXACT = "exact"
    DAY = "day"
    WEEK = "week"
    MONTH = "month"
    YEAR = "year"
    RANGE = "range"
    UNKNOWN = "unknown"


class TemporalMode(str, Enum):
    EVENT = "event"
    STATE = "state"
    SCHEDULED = "scheduled"


class TemporalStatus(str, Enum):
    ASSERTED = "asserted"
    DERIVED = "derived"
    SUPERSEDED = "superseded"
    HISTORICAL = "historical"


class TemporalRelationType(str, Enum):
    BEFORE = "before"
    AFTER = "after"
    SIMULTANEOUS = "simultaneous"
    OVERLAPS = "overlaps"
    DURING = "during"
    CONTAINS = "contains"
    STARTS = "starts"
    ENDS = "ends"
    ADJACENT = "adjacent"


class TemporalResolutionStatus(str, Enum):
    UNRESOLVED = "unresolved"
    RESOLVED_A = "resolved_a"
    RESOLVED_B = "resolved_b"
    SUPERSEDED = "superseded"


class DocumentTimeRole(str, Enum):
    OCCURRENCE = "occurrence"
    DOCUMENTATION = "documentation"
    EFFECTIVE = "effective"
    RECORDED = "recorded"
    OBSERVATION = "observation"
