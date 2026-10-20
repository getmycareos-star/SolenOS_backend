"""
Enums for Notification component in SolenOS.
"""

from enum import Enum


class NotificationType(str, Enum):
    """Type of notification."""
    
    SYSTEM_ALERT = "system_alert"
    """System-generated alerts (maintenance, updates, errors)"""
    
    CARE_UPDATE = "care_update"
    """Updates about care recipient status or condition"""
    
    MEDICATION_REMINDER = "medication_reminder"
    """Reminders for medication administration"""
    
    APPOINTMENT_REMINDER = "appointment_reminder"
    """Reminders for upcoming appointments"""
    
    SYMPTOM_ALERT = "symptom_alert"
    """Alerts about new or changing symptoms"""
    
    EMERGENCY_ALERT = "emergency_alert"
    """Emergency alerts requiring immediate attention"""
    
    CAREGIVER_ALERT = "caregiver_alert"
    """Alerts specifically for caregivers"""
    
    PROFESSIONAL_ALERT = "professional_alert"
    """Alerts specifically for healthcare professionals"""
    
    SHARE_REQUEST = "share_request"
    """Request to share information with another party"""
    
    SHARE_RESPONSE = "share_response"
    """Response to a share request"""
    
    SUMMARY_READY = "summary_ready"
    """Notification that a care summary is ready for viewing"""
    
    WELLBEING_CHECKIN = "wellbeing_checkin"
    """Request for caregiver wellbeing check-in"""
    
    RESPONSE_REQUIRED = "response_required"
    """Notification requiring a response or action"""
    
    INFORMATIONAL = "informational"
    """Purely informational notification"""
    
    WARNING = "warning"
    """Warning about potential issues or risks"""
    
    CRITICAL = "critical"
    """Critical notification requiring immediate attention"""
    
    FOLLOW_UP = "follow_up"
    """Follow-up on previous notification or event"""
    
    COMPLETION = "completion"
    """Notification of task or process completion"""
    
    ERROR = "error"
    """Notification of an error or problem"""
    
    MAINTENANCE = "maintenance"
    """System maintenance notification"""
    
    SECURITY = "security"
    """Security-related notification"""
    
    COMPLIANCE = "compliance"
    """Compliance-related notification"""
    
    AUDIT = "audit"
    """Audit-related notification"""
    
    OTHER = "other"
    """Notification type not fitting other categories"""


class NotificationPriority(str, Enum):
    """Priority level of the notification."""
    
    LOW = "low"
    """Low priority - can be viewed at convenience"""
    
    MEDIUM = "medium"
    """Medium priority - should be viewed soon"""
    
    HIGH = "high"
    """High priority - should be viewed promptly"""
    
    URGENT = "urgent"
    """Urgent priority - requires attention within short timeframe"""
    
    CRITICAL = "critical"
    """Critical priority - requires immediate attention"""
    
    EMERGENCY = "emergency"
    """Emergency priority - life-threatening or critical"""


class NotificationStatus(str, Enum):
    """Status of the notification."""
    
    PENDING = "pending"
    """Notification created but not yet sent"""
    
    QUEUED = "queued"
    """Notification queued for delivery"""
    
    SENDING = "sending"
    """Notification is currently being sent"""
    
    SENT = "sent"
    """Notification has been sent but delivery not confirmed"""
    
    DELIVERED = "delivered"
    """Notification has been successfully delivered"""
    
    FAILED = "failed"
    """Notification delivery failed"""
    
    READ = "read"
    """Notification has been read by recipient"""
    
    ARCHIVED = "archived"
    """Notification has been archived"""
    
    EXPIRED = "expired"
    """Notification has expired and is no longer relevant"""
    
    CANCELLED = "cancelled"
    """Notification was cancelled before sending"""
    
    RECALLED = "recalled"
    """Notification was recalled after sending"""
    
    ACTION_REQUIRED = "action_required"
    """Notification requires action from recipient"""
    
    ACTION_COMPLETED = "action_completed"
    """Required action has been completed"""


class NotificationCategory(str, Enum):
    """Category of notification for organization and filtering."""
    
    CLINICAL = "clinical"
    """Clinical information about care recipient"""
    
    ADMINISTRATIVE = "administrative"
    """Administrative or operational information"""
    
    CAREGIVER_SUPPORT = "caregiver_support"
    """Support and resources for caregivers"""
    
    EMERGENCY = "emergency"
    """Emergency alerts and warnings"""
    
    REMINDER = "reminder"
    """Reminders for appointments, medications, etc."""
    
    SUMMARY = "summary"
    """Care summaries and reports"""
    
    WELLBEING = "wellbeing"
    """Caregiver and care recipient wellbeing"""
    
    MEDICATION = "medication"
    """Medication-related information"""
    
    APPOINTMENT = "appointment"
    """Appointment-related information"""
    
    SHARE = "share"
    """Information sharing requests and responses"""
    
    FEEDBACK = "feedback"
    """Requests for feedback or surveys"""
    
    EDUCATION = "education"
    """Educational content and resources"""
    
    RESOURCE = "resource"
    """Information about available resources"""
    
    COMPLIANCE = "compliance"
    """Compliance and regulatory information"""
    
    SECURITY = "security"
    """Security alerts and warnings"""
    
    MAINTENANCE = "maintenance"
    """System maintenance and updates"""
    
    TECHNICAL = "technical"
    """Technical notifications about system operation"""
    
    SOCIAL = "social"
    """Social and community notifications"""
    
    OTHER = "other"
    """Notification category not fitting other categories"""


class DeliveryMethod(str, Enum):
    """Method of delivery for notifications."""
    
    IN_APP = "in_app"
    """Displayed within the application"""
    
    PUSH_NOTIFICATION = "push_notification"
    """Mobile push notification"""
    
    EMAIL = "email"
    """Sent via email"""
    
    SMS = "sms"
    """Sent via SMS/text message"""
    
    VOICE_CALL = "voice_call"
    """Automated voice call"""
    
    FAX = "fax"
    """Sent via fax"""
    
    POSTAL_MAIL = "postal_mail"
    """Sent via physical mail"""
    
    INTERNAL_MESSAGE = "internal_message"
    """Internal system message"""
    
    SECURE_MESSAGE = "secure_message"
    """Sent via secure messaging system"""
    
    WEBHOOK = "webhook"
    """Sent via webhook to external system"""
    
    HL7 = "hl7"
    """Sent via HL7 interface to healthcare systems"""
    
    FHIR = "fhir"
    """Sent via FHIR API to healthcare systems"""
    
    ELECTRONIC_HEALTH_RECORD = "electronic_health_record"
    """Sent directly to EHR system"""
    
    CAREGIVER_PORTAL = "caregiver_portal"
    """Displayed in caregiver portal"""
    
    PROFESSIONAL_PORTAL = "professional_portal"
    """Displayed in professional portal"""
    
    FAMILY_PORTAL = "family_portal"
    """Displayed in family portal"""
    
    OTHER = "other"
    """Delivery method not fitting other categories"""


class NotificationActionType(str, Enum):
    """Type of action required or suggested by the notification."""
    
    NONE = "none"
    """No action required"""
    
    ACKNOWLEDGE = "acknowledge"
    """Simply acknowledge receipt"""
    
    READ_AND_CONFIRM = "read_and_confirm"
    """Read and confirm understanding"""
    
    PROVIDE_INFORMATION = "provide_information"
    """Provide specific information or data"""
    
    TAKE_MEASUREMENT = "take_measurement"
    """Take a measurement (vitals, weight, etc.)"""
    
    ADMINISTER_MEDICATION = "administer_medication"
    """Administer medication"""
    
    SCHEDULE_APPOINTMENT = "schedule_appointment"
    """Schedule an appointment"""
    
    CONTACT_PROFESSIONAL = "contact_professional"
    """Contact a healthcare professional"""
    
    CONTACT_FAMILY = "contact_family"
    """Contact a family member"""
    
    REVIEW_DOCUMENT = "review_document"
    """Review a document or care summary"""
    
    COMPLETE_ASSESSMENT = "complete_assessment"
    """Complete an assessment or questionnaire"""
    
    UPDATE_CARE_PLAN = "update_care_plan"
    """Update the care plan"""
    
    PROVIDE_FEEDBACK = "provide_feedback"
    """Provide feedback on care or service"""
    
    ATTEND_MEETING = "attend_meeting"
    """Attend a meeting or appointment"""
    
    FOLLOW_UP_INSTRUCTIONS = "follow_up_instructions"
    """Follow specific follow-up instructions"""
    
    EMERGENCY_ACTION = "emergency_action"
    """Take emergency action as specified"""
    
    SEEK_EMERGENCY_CARE = "seek_emergency_care"
    """Seek emergency medical care"""
    
    CONTACT_EMERGENCY_SERVICES = "contact_emergency_services"
    """Contact emergency services (911, etc.)"""
    
    ISOLATE = "isolate"
    """Isolate the care recipient (for infectious disease)"""
    
    QUARANTINE = "quarantine"
    """Quarantine the care recipient"""
    
    RESTRICT_VISITORS = "restrict_visitors"
    """Restrict visitors to the care recipient"""
    
    MONITOR_CLOSELY = "monitor_closely"
    """Monitor the care recipient closely"""
    
    DOCUMENT_OBSERVATIONS = "document_observations"
    """Document observations of the care recipient"""
    
    REVIEW_MEDICATIONS = "review_medications"
    """Review current medications"""
    
    CHECK_VITALS = "check_vitals"
    """Check vital signs"""
    
    ASSESS_PAIN = "assess_pain"
    """Assess pain level"""
    
    ENSURE_SAFETY = "ensure_safety"
    """Ensure safety of care recipient"""
    
    PROVIDE_COMFORT = "provide_comfort"
    """Provide comfort measures"""
    
    ADJUST_ENVIRONMENT = "adjust_environment"
    """Adjust the care recipient's environment"""
    
    PROVIDE_ASSISTANCE = "provide_assistance"
    """Provide physical assistance"""
    
    TRANSFER_CARE = "transfer_care"
    """Transfer care to another provider"""
    
    RESPITE_CARE = "respite_care"
    """Arrange for respite care"""
    
    HOSPICE_CONSIDERATION = "hospice_consideration"
    """Consider hospice care options"""
    
    LEGAL_ACTION = "legal_action"
    """Take legal action as needed"""
    
    FINANCIAL_ARRANGEMENT = "financial_arrangement"
    """Make financial arrangements"""
    
    OTHER = "other"
    """Action type not fitting other categories"""