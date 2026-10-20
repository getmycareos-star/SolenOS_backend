"""
Enums for Situation Information component in SolenOS.
"""

from enum import Enum


class InformationDomain(str, Enum):
    """Clinical/functional domains of information."""
    
    MOBILITY = "mobility"
    """Movement, gait, transfers, balance"""
    
    COGNITION = "cognition"
    """Memory, attention, executive function, orientation"""
    
    BEHAVIOR = "behavior"
    """Mood, agitation, aggression, resistance to care, wandering"""
    
    MEDICATION = "medication"
    """Medications, dosages, schedules, side effects, adherence"""
    
    NUTRITION = "nutrition"
    """Intake, appetite, swallowing, weight, hydration"""
    
    PAIN = "pain"
    """Presence, location, intensity, character of pain"""
    
    SLEEP = "sleep"
    """Sleep patterns, insomnia, hypersomnia, sleep quality"""
    
    SELF_CARE = "self_care"
    """ADLs: bathing, dressing, toileting, feeding, grooming"""
    
    COMMUNICATION = "communication"
    """Speech, language, hearing, expressive/receptive abilities"""
    
    VITAL_SIGNS = "vital_signs"
    """Blood pressure, heart rate, respiratory rate, temperature, oxygen saturation"""
    
    SYMPTOMS = "symptoms"
    """Specific symptoms like nausea, dizziness, shortness of breath, etc."""
    
    FUNCTIONAL_STATUS = "functional_status"
    """Overall functional capacity and independence"""
    
    SOCIAL_INTERACTION = "social_interaction"
    """Social engagement, relationships, isolation, loneliness"""
    
    SAFETY = "safety"
    """Safety risks, fall risk, elopement risk, medication safety"""
    
    ENVIRONMENT = "environment"
    """Living situation, home safety, accessibility, support systems"""
    
    PSYCHOSOCIAL = "psychosocial"
    """Emotional well-being, stress, coping, spiritual needs"""
    
    ADVANCE_DIRECTIVES = "advance_directives"
    """Living wills, power of attorney, code status, treatment preferences"""
    
    FINANCIAL = "financial"
    """Income, expenses, insurance, financial management capacity"""
    
    LEGAL = "legal"
    """Legal capacity, guardianship, conservatorship, legal proceedings"""
    
    OTHER = "other"
    """Information not fitting other domains"""


class InformationStatus(str, Enum):
    """Status of the information."""
    
    PENDING_REVIEW = "pending_review"
    """Information has been collected but not yet reviewed/validated"""
    
    UNDER_REVIEW = "under_review"
    """Information is currently being reviewed"""
    
    REVIEWED = "reviewed"
    """Information has been reviewed but not yet validated"""
    
    VALIDATED = "validated"
    """Information has been reviewed and validated as accurate"""
    
    SUPERSEDED = "superseded"
    """Information has been replaced by newer information"""
    
    ARCHIVED = "archived"
    """Information is no longer active but kept for historical reference"""
    
    DISPUTED = "disputed"
    """Information accuracy is under dispute"""
    
    WITHDRAWN = "withdrawn"
    """Information has been withdrawn or retracted"""


class InformationConfidenceLevel(str, Enum):
    """Level of confidence in the information accuracy."""
    
    VERY_LOW = "very_low"
    """0-20% confidence - minimal reliability"""
    
    LOW = "low"
    """20-40% confidence - limited reliability"""
    
    MODERATE = "moderate"
    """40-60% confidence - moderate reliability"""
    
    HIGH = "high"
    """60-80% confidence - good reliability"""
    
    VERY_HIGH = "very_high"
    """80-100% confidence - high reliability"""
    
    CERTAIN = "certain"
    """Known with certainty (rarely used for clinical information)"""


class InformationSourceType(str, Enum):
    """Type of source for the information."""
    
    DIRECT_OBSERVATION = "direct_observation"
    """Direct observation by caregiver or professional"""
    
    CAREGIVER_REPORT = "caregiver_report"
    """Report from caregiver (family or professional)"""
    
    PROFESSIONAL_ASSESSMENT = "professional_assessment"
    """Formal assessment by healthcare professional"""
    
    MEDICAL_RECORD = "medical_record"
    """Information from medical records or charts"""
    
    MEDICATION_RECORD = "medication_record"
    """Information from medication administration records"""
    
    LABORATORY_RESULT = "laboratory_result"
    """Results from laboratory tests"""
    
    IMAGING_RESULT = "imaging_result"
    """Results from imaging studies (X-ray, MRI, CT, etc.)"""
    
    ASSESSMENT_TOOL = "assessment_tool"
    """Score from standardized assessment tool"""
    
    SELF_REPORT = "self_report"
    """Information reported directly by the care recipient"""
    
    FAMILY_REPORT = "family_report"
    """Report from family members"""
    
    OBSERVATION_TOOL = "observation_tool"
    """Data from monitoring devices or observation tools"""
    
    ELECTRONIC_HEALTH_RECORD = "electronic_health_record"
    """Information from EHR systems"""
    
    TELEHEALTH = "telehealth"
    """Information gathered through telehealth encounters"""
    
    WEARABLE_DEVICE = "wearable_device"
    """Data from wearable monitoring devices"""
    
    ENVIRONMENTAL_ASSESSMENT = "environmental_assessment"
    """Assessment of living environment or safety"""
    
    THIRD_PARTY_REPORT = "third_party_report"
    """Information from third parties (neighbors, friends, etc.)"""
    
    HISTORICAL_RECORD = "historical_record"
    """Information from historical records or prior encounters"""
    
    CLINICAL_NOTE = "clinical_note"
    """Note from clinical encounter or consultation"""
    
    DISCHARGE_SUMMARY = "discharge_summary"
    """Hospital or facility discharge summary"""
    
    CONSULTATION_NOTE = "consultation_note"
    """Note from specialist consultation"""
    
    OTHER = "other"
    """Source not fitting other categories"""


class InformationValidationStatus(str, Enum):
    """Validation status of the information."""
    
    NOT_VALIDATED = "not_validated"
    """Information has not undergone validation"""
    
    VALIDATION_PENDING = "validation_pending"
    """Validation is pending or in progress"""
    
    PARTIALLY_VALIDATED = "partially_validated"
    """Some aspects validated, others pending"""
    
    VALIDATED = "validated"
    """Information has been validated as accurate"""
    
    VALIDATED_WITH_QUALIFICATIONS = "validated_with_qualifications"
    """Validated but with noted limitations or qualifications"""
    
    INVALIDATED = "invalidated"
    """Information has been determined to be inaccurate"""
    
    CONTRADICTED = "contradicted"
    """Information contradicted by more reliable evidence"""
    
    SUPERSEDED_BY_EVIDENCE = "superseded_by_evidence"
    """Superseded by newer, more reliable evidence"""