from enum import Enum


class EvidenceType(str, Enum):
    """Controlled vocabulary for evidence types. Requirement 5, 6."""
    # Observations
    OBSERVATION = "observation"
    COGNITIVE_OBSERVATION = "cognitive_observation"
    MOBILITY_OBSERVATION = "mobility_observation"
    SLEEP_OBSERVATION = "sleep_observation"
    APPETITE_OBSERVATION = "appetite_observation"
    BEHAVIOURAL_OBSERVATION = "behavioural_observation"
    FUNCTIONAL_OBSERVATION = "functional_observation"

    # Reported facts
    REPORTED_FACT = "reported_fact"
    DOCUMENTED_FACT = "documented_fact"

    # Measurements
    MEASUREMENT = "measurement"
    BLOOD_PRESSURE = "blood_pressure"
    WEIGHT = "weight"
    TEMPERATURE = "temperature"
    GLUCOSE = "glucose"
    PULSE = "pulse"
    SPO2 = "spo2"
    HEART_RATE = "heart_rate"

    # Clinical statements
    CLINICAL_STATEMENT = "clinical_statement"
    DIAGNOSIS_STATED = "diagnosis_stated"
    SUSPECTED_CONDITION = "suspected_condition"
    RULED_OUT_CONDITION = "ruled_out_condition"

    # Medication events
    MEDICATION_EVENT = "medication_event"
    MEDICATION_MENTIONED = "medication_mentioned"
    MEDICATION_PRESCRIBED = "medication_prescribed"
    MEDICATION_STARTED = "medication_started"
    MEDICATION_STOPPED = "medication_stopped"
    MEDICATION_DOSE_CHANGED = "medication_dose_changed"
    MEDICATION_TAKEN = "medication_taken"
    MEDICATION_MISSED = "medication_missed"
    MEDICATION_REFUSED = "medication_refused"
    MEDICATION_INSTRUCTION = "medication_instruction"

    # Appointment events
    APPOINTMENT_EVENT = "appointment_event"
    APPOINTMENT_SCHEDULED = "appointment_scheduled"
    APPOINTMENT_COMPLETED = "appointment_completed"
    APPOINTMENT_CANCELLED = "appointment_cancelled"
    APPOINTMENT_MISSED = "appointment_missed"
    APPOINTMENT_INSTRUCTION = "appointment_instruction"

    # Procedure events
    PROCEDURE_EVENT = "procedure_event"
    PROCEDURE_SCHEDULED = "procedure_scheduled"
    PROCEDURE_COMPLETED = "procedure_completed"
    PROCEDURE_CANCELLED = "procedure_cancelled"

    # Care actions
    CARE_ACTION = "care_action"
    CARE_PLAN_ITEM = "care_plan_item"
    INTERVENTION = "intervention"
    TREATMENT_RESPONSE = "treatment_response"

    # Outcomes
    OUTCOME = "outcome"
    SYMPTOM_REPORT = "symptom_report"
    OUTCOME_IMPROVED = "outcome_improved"
    OUTCOME_WORSENED = "outcome_worsened"
    OUTCOME_UNCHANGED = "outcome_unchanged"

    # Communication
    COMMUNICATION = "communication"
    PROVIDER_COMMUNICATION = "provider_communication"
    CAREGIVER_COMMUNICATION = "caregiver_communication"
    PATIENT_COMMUNICATION = "patient_communication"

    # Decisions
    DECISION = "decision"
    CARE_DECISION = "care_decision"
    TREATMENT_DECISION = "treatment_decision"
    ADMINISTRATIVE_DECISION = "administrative_decision"

    # Instructions
    INSTRUCTION = "instruction"
    PROVIDER_INSTRUCTION = "provider_instruction"
    CAREGIVER_INSTRUCTION = "caregiver_instruction"
    DISCHARGE_INSTRUCTION = "discharge_instruction"

    # Questions
    QUESTION = "question"
    CAREGIVER_QUESTION = "caregiver_question"
    PATIENT_QUESTION = "patient_question"
    CLINICAL_QUESTION = "clinical_question"

    # Temporal facts
    TEMPORAL_FACT = "temporal_fact"
    ONSET = "onset"
    CESSATION = "cessation"
    DURATION = "duration"
    FREQUENCY = "frequency"

    # Relationship facts
    RELATIONSHIP_FACT = "relationship_fact"
    CAREGIVER_RELATIONSHIP = "caregiver_relationship"
    PROVIDER_RELATIONSHIP = "provider_relationship"
    FAMILY_RELATIONSHIP = "family_relationship"

    # Administrative facts
    ADMINISTRATIVE_FACT = "administrative_fact"
    INSURANCE_INFO = "insurance_info"
    LEGAL_DOCUMENT = "legal_document"
    ADVANCE_DIRECTIVE = "advance_directive"

    # Source metadata
    SOURCE_METADATA = "source_metadata"
    DOCUMENT_METADATA = "document_metadata"
    UPLOAD_METADATA = "upload_metadata"

    # Other
    OTHER_SUPPORTED_EVIDENCE = "other_supported_evidence"

    # Backward-compatible aliases (used by legacy test/service code)
    NOTE = "note"
    MEDICATION = "medication"
    DAILY_CARE_ENTRY = "daily_care_entry"
    APPOINTMENT = "appointment"
    VITAL_SIGN = "vital_sign"
    REPORTED = "reported"
    DOCUMENTED = "documented"


class EvidenceStatus(str, Enum):
    """Evidence status machine. Requirement 42."""
    ACTIVE = "active"
    UNCERTAIN = "uncertain"
    CONFLICTED = "conflicted"
    SUPERSEDED = "superseded"
    INVALIDATED = "invalidated"
    DUPLICATE = "duplicate"
    UNRESOLVED = "unresolved"
    HISTORICAL = "historical"
    PENDING_VALIDATION = "pending_validation"
    RECORDED = "recorded"
    REPORTED = "reported"


class EvidenceSubjectRole(str, Enum):
    """Who/what the evidence is about. Requirement 7."""
    CARE_RECIPIENT = "care_recipient"
    CAREGIVER = "caregiver"
    PROVIDER = "provider"
    ORGANIZATION = "organization"
    MEDICATION = "medication"
    CONDITION = "condition"
    DEVICE = "device"
    LOCATION = "location"
    OTHER = "other"


class EvidenceObserverRole(str, Enum):
    """Who observed/reported this. Requirement 8."""
    CARE_RECIPIENT = "care_recipient"
    CAREGIVER = "caregiver"
    PROVIDER = "provider"
    NURSE = "nurse"
    PHYSICIAN = "physician"
    PHARMACIST = "pharmacist"
    THERAPIST = "therapist"
    FAMILY_MEMBER = "family_member"
    FRIEND = "friend"
    CARE_RECIPIENT_SELF_REPORT = "care_recipient_self_report"
    SYSTEM = "system"
    DEVICE = "device"
    UNKNOWN = "unknown"


class EvidenceAuthorRole(str, Enum):
    """Who authored the source. Requirement 9."""
    CARE_RECIPIENT = "care_recipient"
    CAREGIVER = "caregiver"
    PROVIDER = "provider"
    NURSE = "nurse"
    PHYSICIAN = "physician"
    PHARMACIST = "pharmacist"
    THERAPIST = "therapist"
    FAMILY_MEMBER = "family_member"
    FRIEND = "friend"
    SYSTEM = "system"
    DEVICE = "device"
    UNKNOWN = "unknown"


class ConfidenceDimension(str, Enum):
    """Multi-dimensional confidence. Requirement 17."""
    EXTRACTION = "extraction"
    ENTITY_RESOLUTION = "entity_resolution"
    TEMPORAL = "temporal"
    SOURCE_SUPPORT = "source_support"
    INTERPRETATION = "interpretation"
    NORMALIZATION = "normalization"


class TemporalGranularity(str, Enum):
    """Temporal precision. Requirement 14."""
    EXACT_TIMESTAMP = "exact_timestamp"
    DATE = "date"
    MONTH_YEAR = "month_year"
    RELATIVE = "relative"
    APPROXIMATE = "approximate"
    TIME_RANGE = "time_range"
    UNKNOWN = "unknown"


class TemporalAnchorType(str, Enum):
    """What the temporal reference anchors to. Requirement 15."""
    EVENT_TIME = "event_time"
    OBSERVATION_TIME = "observation_time"
    DOCUMENT_TIME = "document_time"
    AUTHORING_TIME = "authoring_time"
    SOURCE_TIMESTAMP = "source_timestamp"
    UPLOAD_TIME = "upload_time"
    INGESTION_TIME = "ingestion_time"
    PROCESSING_TIME = "processing_time"
    RELATIVE_EXPRESSION = "relative_expression"


class SourceType(str, Enum):
    """Source type classification. Requirement 18, 19."""
    MANUAL_CAREGIVER_INPUT = "manual_caregiver_input"
    MANUAL_CARE_RECIPIENT_INPUT = "manual_care_recipient_input"
    MANUAL_PROVIDER_INPUT = "manual_provider_input"
    FREE_FORM_NOTE = "free_form_note"
    STRUCTURED_FORM = "structured_form"
    DOCUMENT_PDF = "document_pdf"
    DOCUMENT_DOCX = "document_docx"
    DOCUMENT_TXT = "document_txt"
    DOCUMENT_IMAGE = "document_image"
    DOCUMENT_SCAN = "document_scan"
    EMAIL = "email"
    STRUCTURED_RECORD = "structured_record"
    EXTERNAL_RECORD = "external_record"
    PHOTOGRAPH = "photograph"
    OCR_RESULT = "ocr_result"
    VOICE_TRANSCRIPT = "voice_transcript"
    DEVICE_READING = "device_reading"
    SYSTEM_GENERATED = "system_generated"
    REPORTED_OBSERVATION = "reported_observation"
    CAREGIVER_OBSERVATION = "caregiver_observation"


class EvidenceRelationshipType(str, Enum):
    """Relationship types between evidence. Requirement 32."""
    SUPPORTS = "supports"
    CONTRADICTS = "contradicts"
    CORROBORATES = "corroborates"
    DERIVED_FROM = "derived_from"
    REFERS_TO = "refers_to"
    ABOUT = "about"
    PRECEDES = "precedes"
    FOLLOWS = "follows"
    RESULTS_FROM = "results_from"
    ASSOCIATED_WITH = "associated_with"
    UPDATES = "updates"
    SUPERSEDES = "supersedes"
    CORRECTS = "corrects"
    INVALIDATES = "invalidates"
    DUPLICATES = "duplicates"
    POTENTIALLY_DUPLICATES = "potentially_duplicates"
    SAME_UNDERLYING_INFORMATION = "same_underlying_information"
    CORROBORATES_WITH = "corroborates_with"


class NormalizationType(str, Enum):
    """What was normalized. Requirement 11, 12."""
    MEASUREMENT = "measurement"
    MEDICATION_NAME = "medication_name"
    CONDITION_NAME = "condition_name"
    PROVIDER_NAME = "provider_name"
    DATE_TIME = "date_time"
    DOSAGE = "dosage"
    UNIT = "unit"
    NONE = "none"


class ExtractionMethod(str, Enum):
    """How evidence was extracted. Requirement 87."""
    MANUAL_ENTRY = "manual_entry"
    STRUCTURED_FORM = "structured_form"
    REGEX_EXTRACTION = "regex_extraction"
    LLM_EXTRACTION = "llm_extraction"
    OCR_TESSERACT = "ocr_tesseract"
    PDF_TEXT_EXTRACTION = "pdf_text_extraction"
    DOCX_PARSING = "docx_parsing"
    VOICE_TRANSCRIPTION = "voice_transcription"
    DEVICE_API = "device_api"
    EXTERNAL_API = "external_api"


class SourceAuthority(str, Enum):
    """Source authority for ranking. Requirement 18, 58."""
    CLINICIAN = "clinician"
    LABORATORY = "laboratory"
    MEDICATION_RECORD = "medication_record"
    DISCHARGE_INSTRUCTION = "discharge_instruction"
    EXTERNAL_GUIDELINE = "external_guideline"
    CAREGIVER_REPORT = "caregiver_report"
    PATIENT_REPORT = "patient_report"
    SYSTEM_INFERENCE = "system_inference"
    DOCUMENT = "document"
    UNKNOWN = "unknown"


class NegationStatus(str, Enum):
    """Negation handling. Requirement 47."""
    ASSERTED = "asserted"
    NEGATED = "negated"
    UNCERTAIN = "uncertain"
    HEDGED = "hedged"
    QUOTED = "quoted"
    SECOND_HAND = "second_hand"


class UncertaintyLevel(str, Enum):
    """Uncertainty preservation. Requirement 48."""
    CERTAIN = "certain"
    PROBABLE = "probable"
    POSSIBLE = "possible"
    UNLIKELY = "unlikely"
    UNKNOWN = "unknown"
    EXPLICITLY_UNCERTAIN = "explicitly_uncertain"


class HedgeType(str, Enum):
    """Hedging preservation. Requirement 49."""
    NONE = "none"
    APPEARS = "appears"
    POSSIBLY = "possibly"
    MIGHT = "might"
    SEEMS = "seems"
    I_THINK = "i_think"
    NOT_SURE = "not_sure"
    MAY = "may"
    COULD = "could"
    SUGGESTS = "suggests"
    INDICATES = "indicates"


class EntityResolutionStatus(str, Enum):
    """Entity resolution status. Requirement 10, 70."""
    RESOLVED = "resolved"
    UNRESOLVED = "unresolved"
    AMBIGUOUS = "ambiguous"
    USER_CORRECTED = "user_corrected"
    NOT_APPLICABLE = "not_applicable"


class CorrectionType(str, Enum):
    """Correction classification. Requirement 38, 39."""
    SUBJECT_CORRECTION = "subject_correction"
    TEMPORAL_CORRECTION = "temporal_correction"
    CONTENT_CORRECTION = "content_correction"
    ENTITY_RESOLUTION_CORRECTION = "entity_resolution_correction"
    EXTRACTION_CORRECTION = "extraction_correction"
    SOURCE_CORRECTION = "source_correction"
    RELATIONSHIP_CORRECTION = "relationship_correction"


class InvalidationReason(str, Enum):
    """Invalidation reasons. Requirement 36."""
    WRONG_PATIENT = "wrong_patient"
    CORRUPTED_EXTRACTION = "corrupted_extraction"
    DUPLICATE_MISTAKEN_AS_INDEPENDENT = "duplicate_mistaken_as_independent"
    USER_IDENTIFIED_WRONG_SOURCE = "user_identified_wrong_source"
    SOURCE_PROVEN_ERRONEOUS = "source_proven_erroneous"
    EXTRACTION_BUG = "extraction_bug"
    INCORRECT_ENTITY_ASSIGNMENT = "incorrect_entity_assignment"
    SOURCE_RETRACTED = "source_retracted"
    OTHER = "other"


class SupersessionReason(str, Enum):
    """Supersession reasons. Requirement 34."""
    EXPLICIT_UPDATE = "explicit_update"
    DOSE_CHANGE = "dose_change"
    MEDICATION_CHANGE = "medication_change"
    CONDITION_UPDATE = "condition_update"
    APPOINTMENT_RESCHEDULE = "appointment_reschedule"
    NEW_INFORMATION = "new_information"
    CLINICAL_DECISION = "clinical_decision"
    CAREGIVER_UPDATE = "caregiver_update"


class VerificationAction(str, Enum):
    """Verification actions for verification moments. Part I of SOLOS specification."""
    CONFIRM_CORRECT = "confirm_correct"
    CORRECT = "correct"
    MARK_UNKNOWN = "mark_unknown"
    IDENTIFY_CONTRADICTION = "identify_contradiction"
    ADD_MISSING_CONTEXT = "add_missing_context"
    CORRECT_DATE = "correct_date"
    CORRECT_PERSON = "correct_person"
    CORRECT_MEDICATION = "correct_medication"
    CORRECT_SOURCE = "correct_source"
    REJECT_INFERENCE = "reject_inference"
    CONFIRM_INFERENCE = "confirm_inference"
    ADD_SOURCE = "add_source"
    MARK_STALE = "mark_stale"
    MARK_STILL_CURRENT = "mark_still_current"