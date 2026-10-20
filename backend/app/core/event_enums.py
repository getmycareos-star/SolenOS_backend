from enum import Enum


class CareEventType(str, Enum):
    """Controlled vocabulary for care event types. Requirement 14."""
    # Symptom & Observation Events
    SYMPTOM_EVENT = "symptom_event"
    OBSERVATION_EVENT = "observation_event"
    COGNITIVE_OBSERVATION = "cognitive_observation"
    MOBILITY_OBSERVATION = "mobility_observation"
    BEHAVIOURAL_OBSERVATION = "behavioural_observation"
    FUNCTIONAL_OBSERVATION = "functional_observation"
    SLEEP_OBSERVATION = "sleep_observation"
    APPETITE_OBSERVATION = "appetite_observation"

    # Fall & Injury Events
    FALL_EVENT = "fall_event"
    NEAR_FALL_EVENT = "near_fall_event"
    FALL_RISK_EVENT = "fall_risk_event"
    INJURY_EVENT = "injury_event"

    # Hospitalization & Emergency Events
    HOSPITALIZATION_EVENT = "hospitalization_event"
    EMERGENCY_EVENT = "emergency_event"
    ADMISSION_EVENT = "admission_event"
    DISCHARGE_EVENT = "discharge_event"
    TRANSFER_EVENT = "transfer_event"
    ED_VISIT_EVENT = "ed_visit_event"

    # Appointment & Visit Events
    APPOINTMENT_EVENT = "appointment_event"
    VISIT_EVENT = "visit_event"
    FOLLOW_UP_EVENT = "follow_up_event"

    # Diagnostic & Measurement Events
    DIAGNOSTIC_EVENT = "diagnostic_event"
    LAB_EVENT = "lab_event"
    MEASUREMENT_EVENT = "measurement_event"
    BLOOD_PRESSURE_MEASUREMENT = "blood_pressure_measurement"
    WEIGHT_MEASUREMENT = "weight_measurement"
    TEMPERATURE_MEASUREMENT = "temperature_measurement"
    GLUCOSE_MEASUREMENT = "glucose_measurement"

    # Medication Events
    MEDICATION_PRESCRIPTION_EVENT = "medication_prescription_event"
    MEDICATION_START_EVENT = "medication_start_event"
    MEDICATION_STOP_EVENT = "medication_stop_event"
    MEDICATION_CHANGE_EVENT = "medication_change_event"
    MEDICATION_DOSE_CHANGE_EVENT = "medication_dose_change_event"
    MEDICATION_MISSED_EVENT = "medication_missed_event"
    MEDICATION_REFUSED_EVENT = "medication_refused_event"
    MEDICATION_RESUMED_EVENT = "medication_resumed_event"
    MEDICATION_TAKEN_EVENT = "medication_taken_event"
    MEDICATION_INSTRUCTION_EVENT = "medication_instruction_event"

    # Procedure & Therapy Events
    PROCEDURE_EVENT = "procedure_event"
    SURGERY_EVENT = "surgery_event"
    THERAPY_EVENT = "therapy_event"
    REHABILITATION_EVENT = "rehabilitation_event"

    # Care Action & Plan Events
    CARE_ACTION_EVENT = "care_action_event"
    CARE_PLAN_EVENT = "care_plan_event"
    CARE_RECOMMENDATION_EVENT = "care_recommendation_event"
    CARE_INSTRUCTION_EVENT = "care_instruction_event"
    CARE_DECISION_EVENT = "care_decision_event"

    # Referral & Coordination Events
    REFERRAL_EVENT = "referral_event"
    CARE_COORDINATION_EVENT = "care_coordination_event"
    COMMUNICATION_EVENT = "communication_event"
    PROVIDER_COMMUNICATION_EVENT = "provider_communication_event"
    CAREGIVER_COMMUNICATION_EVENT = "caregiver_communication_event"

    # Outcome & State Change Events
    OUTCOME_EVENT = "outcome_event"
    FUNCTIONAL_CHANGE_EVENT = "functional_change_event"
    COGNITIVE_CHANGE_EVENT = "cognitive_change_event"
    BEHAVIOURAL_CHANGE_EVENT = "behavioural_change_event"

    # Backward-compatible aliases (used by legacy service code)
    OBSERVATION = "observation"
    MEDICATION = "medication"
    VITAL_SIGN = "vital_sign"
    FALL = "fall"
    SLEEP = "sleep"
    APPETITE = "appetite"
    APPOINTMENT = "appointment"
    MOBILITY = "mobility"
    ILLNESS = "illness"
    PREFERENCE = "preference"
    COGNITIVE = "cognitive"
    BEHAVIOURAL = "behavioural"
    FUNCTIONAL = "functional"
    FATIGUE = "fatigue"
    CONFUSION = "confusion"
    ALTERED_MENTAL_STATUS = "altered_mental_status"

    # Daily capture scale event types (American spelling for API compatibility)
    BEHAVIOR_AGITATION = "behavior_agitation"
    BEHAVIOR_CONFUSION = "behavior_confusion"
    VITAL_SIGNS = "vital_signs"

    # Administrative Events
    ADMINISTRATIVE_EVENT = "administrative_event"
    INSURANCE_EVENT = "insurance_event"
    LEGAL_EVENT = "legal_event"
    ADVANCE_DIRECTIVE_EVENT = "advance_directive_event"

    # Aggregate/Recurrence Assertions (Requirement 78)
    RECURRENCE_ASSERTION = "recurrence_assertion"
    AGGREGATE_OCCURRENCE = "aggregate_occurrence"

    # Other
    OTHER_CARE_EVENT = "other_care_event"


class CareEventSubtype(str, Enum):
    """Event subtypes for material reasoning improvement. Requirement 16."""
    # Fall subtypes
    FALL_WITNESSED = "witnessed"
    FALL_UNWITNESSED = "unwitnessed"
    FALL_REPORTED = "reported"
    FALL_WITNESSED_BY_CAREGIVER = "witnessed_by_caregiver"
    FALL_WITNESSED_BY_PROVIDER = "witnessed_by_provider"

    # Medication event subtypes
    MEDICATION_STARTED = "started"
    MEDICATION_STOPPED = "stopped"
    MEDICATION_CHANGED = "changed"
    MEDICATION_DOSE_INCREASED = "dose_increased"
    MEDICATION_DOSE_DECREASED = "dose_decreased"
    MEDICATION_MISSED = "missed"
    MEDICATION_REFUSED = "refused"
    MEDICATION_RESUMED = "resumed"
    MEDICATION_TAKEN = "taken"
    MEDICATION_HELD = "held"

    # Appointment subtypes
    APPOINTMENT_SCHEDULED = "scheduled"
    APPOINTMENT_COMPLETED = "completed"
    APPOINTMENT_CANCELLED = "cancelled"
    APPOINTMENT_MISSED = "missed"
    APPOINTMENT_RESCHEDULED = "rescheduled"
    APPOINTMENT_NO_SHOW = "no_show"

    # Hospitalization subtypes
    ADMISSION_PLANNED = "planned"
    ADMISSION_EMERGENCY = "emergency"
    ADMISSION_TRANSFER = "transfer"
    DISCHARGE_HOME = "home"
    DISCHARGE_FACILITY = "facility"
    DISCHARGE_AMA = "against_medical_advice"
    DISCHARGE_DECEASED = "deceased"

    # Procedure subtypes
    PROCEDURE_SCHEDULED = "scheduled"
    PROCEDURE_COMPLETED = "completed"
    PROCEDURE_CANCELLED = "cancelled"
    PROCEDURE_FAILED = "failed"

    # Care plan subtypes
    CARE_PLAN_CREATED = "created"
    CARE_PLAN_UPDATED = "updated"
    CARE_PLAN_COMPLETED = "completed"
    CARE_PLAN_DISCONTINUED = "discontinued"

    # Referral subtypes
    REFERRAL_MADE = "made"
    REFERRAL_ACCEPTED = "accepted"
    REFERRAL_DECLINED = "declined"
    REFERRAL_COMPLETED = "completed"

    # Communication subtypes
    COMMUNICATION_INBOUND = "inbound"
    COMMUNICATION_OUTBOUND = "outbound"
    COMMUNICATION_MEETING = "meeting"
    COMMUNICATION_PHONE = "phone"
    COMMUNICATION_SECURE_MESSAGE = "secure_message"

    # Outcome subtypes
    OUTCOME_IMPROVED = "improved"
    OUTCOME_WORSENED = "worsened"
    OUTCOME_UNCHANGED = "unchanged"
    OUTCOME_RESOLVED = "resolved"
    OUTCOME_UNKNOWN = "unknown"

    # Aggregate assertion subtypes
    RECURRENCE_FREQUENCY_INCREASE = "frequency_increase"
    RECURRENCE_FREQUENCY_DECREASE = "frequency_decrease"
    RECURRENCE_CLUSTERING = "clustering"
    AGGREGATE_COUNT = "count"
    AGGREGATE_RATE = "rate"


class CareEventStatus(str, Enum):
    """Event lifecycle statuses. Requirement 24."""
    # Planned/Scheduled
    PLANNED = "planned"
    SCHEDULED = "scheduled"
    TENTATIVE = "tentative"

    # In Progress
    IN_PROGRESS = "in_progress"
    ONGOING = "ongoing"

    # Completed/Resolved
    COMPLETED = "completed"
    RESOLVED = "resolved"

    # Negative outcomes
    CANCELLED = "cancelled"
    FAILED = "failed"
    PARTIALLY_COMPLETED = "partially_completed"
    NO_SHOW = "no_show"

    # Uncertain/Contested
    UNKNOWN = "unknown"
    UNRESOLVED = "unresolved"
    CONTESTED = "contested"
    UNCONFIRMED = "unconfirmed"
    RECORDED = "recorded"

    # Lifecycle transitions
    SUPERSEDED = "superseded"
    INVALIDATED = "invalidated"
    CORRECTED = "corrected"
    RESCHEDULED = "rescheduled"

    # Historical
    HISTORICAL = "historical"
    ARCHIVED = "archived"


class CareEventSeverity(str, Enum):
    """Event-type-specific severity. Requirement 25."""
    UNKNOWN = "unknown"
    NONE = "none"
    MILD = "mild"
    MODERATE = "moderate"
    SEVERE = "severe"
    CRITICAL = "critical"
    LIFE_THREATENING = "life_threatening"


class CareEventRelationshipType(str, Enum):
    """Typed relationships between care events. Requirement 46."""
    # Temporal ordering
    BEFORE = "before"
    AFTER = "after"
    DURING = "during"
    OVERLAPS = "overlaps"
    PRECEDES = "precedes"
    FOLLOWS = "follows"
    IMMEDIATELY_PRECEDES = "immediately_precedes"
    IMMEDIATELY_FOLLOWS = "immediately_follows"
    SIMULTANEOUS = "simultaneous"
    CONTAINS = "contains"
    EQUAL = "equal"

    # Causal/Mechanistic (only when supported by evidence - Requirement 47)
    TRIGGERS = "triggers"
    RESPONDS_TO = "responds_to"
    RESULTS_IN = "results_in"
    CAUSED_BY = "caused_by"
    CONTRIBUTES_TO = "contributes_to"
    PREVENTS = "prevents"
    MITIGATES = "mitigates"
    EXACERBATES = "exacerbates"

    # Associative
    ASSOCIATED_WITH = "associated_with"
    RELATED_TO = "related_to"
    PART_OF = "part_of"
    HAS_PART = "has_part"
    EPISODE_OF = "episode_of"
    ENCAPSULATES = "encapsulates"

    # Recurrence
    RECURS_WITH = "recurs_with"
    FOLLOWED_BY = "followed_by"
    REPEAT_OF = "repeat_of"
    FIRST_OCCURRENCE = "first_occurrence"
    LAST_OCCURRENCE = "last_occurrence"

    # Correction/Lifecycle
    DUPLICATES = "duplicates"
    POSSIBLE_DUPLICATE = "possible_duplicate"
    CORRECTS = "corrects"
    SUPERSEDES = "supersedes"
    CONTRADICTS = "contradicts"
    RESOLVES = "resolves"
    REOPENS = "reopens"

    # Dependency
    DEPENDS_ON = "depends_on"
    PREREQUISITE_FOR = "prerequisite_for"
    ENABLES = "enables"
    BLOCKS = "blocks"


class CareEventGroupingType(str, Enum):
    """Event grouping types. Requirement 48."""
    EPISODE = "episode"
    ENCOUNTER = "encounter"
    HOSPITALISATION = "hospitalisation"
    CARE_CYCLE = "care_cycle"
    TREATMENT_COURSE = "treatment_course"
    RECURRENCE_GROUP = "recurrence_group"
    FOLLOW_UP_CHAIN = "follow_up_chain"
    MOBILITY_THREAD = "mobility_thread"
    MEDICATION_THREAD = "medication_thread"
    COGNITIVE_THREAD = "cognitive_thread"
    SYMPTOM_THREAD = "symptom_thread"


class EventStateImpactType(str, Enum):
    """Types of state impact from events. Requirement 96."""
    CREATES = "creates"
    CHANGES = "changes"
    ENDS = "ends"
    SUPPORTS = "supports"
    CONTRADICTS = "contradicts"
    REINFORCES = "reinforces"
    REOPENS = "reopens"
    UNKNOWN = "unknown"


class EventStateImpactConfidence(str, Enum):
    """Confidence levels for state impact."""
    SUPPORTED = "supported"
    POSSIBLE = "possible"
    NO_ESTABLISHED_IMPACT = "no_established_impact"
    UNKNOWN = "unknown"


class EventDetectionConfidenceDimension(str, Enum):
    """Multi-dimensional confidence for event detection. Requirement 35."""
    EVENT_DETECTION = "event_detection"
    EVENT_TYPE = "event_type"
    SUBJECT = "subject"
    TEMPORAL = "temporal"
    IDENTITY = "identity"
    SEVERITY = "severity"
    STATUS = "status"
    OUTCOME = "outcome"
    OVERALL = "overall"


class EventMentionDeduplicationStatus(str, Enum):
    """Status of event mention deduplication. Requirement 42."""
    DISTINCT = "distinct"
    SAME_EVENT = "same_event"
    POSSIBLE_DUPLICATE = "possible_duplicate"
    UNRESOLVED = "unresolved"


class EventCorrectionType(str, Enum):
    """Types of event corrections. Requirement 43."""
    TEMPORAL_CORRECTION = "temporal_correction"
    TYPE_CORRECTION = "type_correction"
    SEVERITY_CORRECTION = "severity_correction"
    STATUS_CORRECTION = "status_correction"
    SUBJECT_CORRECTION = "subject_correction"
    CONTENT_CORRECTION = "content_correction"
    SPLIT_EVENT = "split_event"
    MERGE_EVENTS = "merge_events"


class EventInvalidationReason(str, Enum):
    """Reasons for event invalidation. Requirement 44."""
    WRONG_PATIENT = "wrong_patient"
    CORRUPTED_EXTRACTION = "corrupted_extraction"
    DUPLICATE_MISTAKEN_AS_INDEPENDENT = "duplicate_mistaken_as_independent"
    USER_IDENTIFIED_WRONG_SOURCE = "user_identified_wrong_source"
    SOURCE_PROVEN_ERRONEOUS = "source_proven_erroneous"
    EXTRACTION_BUG = "extraction_bug"
    INCORRECT_ENTITY_ASSIGNMENT = "incorrect_entity_assignment"
    SOURCE_RETRACTED = "source_retracted"
    NEVER_OCCURRED = "never_occurred"
    OTHER = "other"


class EventSupersessionReason(str, Enum):
    """Reasons for event supersession. Requirement 45."""
    EXPLICIT_UPDATE = "explicit_update"
    NEW_INFORMATION = "new_information"
    CAREGIVER_UPDATE = "caregiver_update"
    CLINICAL_DECISION = "clinical_decision"
    CORRECTION_APPLIED = "correction_applied"
    RESCHEDULE = "reschedule"


class EventLifecycleStage(str, Enum):
    """Event lifecycle stages. Requirement 64."""
    DETECTED = "detected"
    VALIDATED = "validated"
    ACTIVE = "active"
    COMPLETED = "completed"
    RESOLVED = "resolved"
    SUPERSEDED = "superseded"
    INVALIDATED = "invalidated"
    ARCHIVED = "archived"


class EventTimeGranularity(str, Enum):
    """Temporal granularity for event time (delegates to Temporal Intelligence)."""
    EXACT_TIMESTAMP = "exact_timestamp"
    DATE = "date"
    MONTH_YEAR = "month_year"
    RELATIVE = "relative"
    APPROXIMATE = "approximate"
    TIME_RANGE = "time_range"
    UNKNOWN = "unknown"
    ONGOING = "ongoing"


class EventDurationType(str, Enum):
    """Event duration types. Requirement 27."""
    INSTANTANEOUS = "instantaneous"
    SHORT_LIVED = "short_lived"
    INTERVAL_BASED = "interval_based"
    ONGOING = "ongoing"
    UNKNOWN_DURATION = "unknown_duration"
    OPEN_DURATION = "open_duration"


class EventSubjectRole(str, Enum):
    """Role of the event subject. Requirement 31."""
    CARE_RECIPIENT = "care_recipient"
    CAREGIVER = "caregiver"
    PROVIDER = "provider"
    ORGANIZATION = "organization"
    FACILITY = "facility"
    FAMILY_MEMBER = "family_member"
    EMERGENCY_SERVICE = "emergency_service"
    UNKNOWN = "unknown"


class EventParticipantRole(str, Enum):
    """Roles of event participants. Requirement 30."""
    SUBJECT = "subject"
    CAREGIVER = "caregiver"
    PROVIDER = "provider"
    FAMILY_MEMBER = "family_member"
    FACILITY = "facility"
    ORGANIZATION = "organization"
    EMERGENCY_SERVICE = "emergency_service"
    WITNESS = "witness"
    INFORMANT = "informant"


class RecurrenceDetectionMethod(str, Enum):
    """Methods for detecting recurrence. Requirement 39."""
    TEMPORAL_CLUSTERING = "temporal_clustering"
    SEMANTIC_SIMILARITY = "semantic_similarity"
    SUBJECT_TYPE_MATCH = "subject_type_match"
    CONTEXTUAL_SIMILARITY = "contextual_similarity"
    SOURCE_CORROBORATION = "source_corroboration"
    USER_CONFIRMED = "user_confirmed"


class EventMentionType(str, Enum):
    """Classification of how an event appears in source. Requirement 75."""
    CURRENT_OCCURRENCE = "current_occurrence"
    HISTORICAL_REFERENCE = "historical_reference"
    PLANNED_FUTURE = "planned_future"
    HYPOTHETICAL = "hypothetical"
    NEGATIVE_ASSERTION = "negative_assertion"
    REPEATED_MENTION = "repeated_mention"
    SUMMARY_ASSERTION = "summary_assertion"
    COPY_FORWARD = "copy_forward"