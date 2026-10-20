"""Enums for the Care Responsibility Handoff system."""

from enum import Enum


class HandoffLifecycleState(str, Enum):
    DRAFT = "draft"
    PROPOSED = "proposed"
    ACCEPTED = "accepted"
    ACTIVE = "active"
    PAUSED = "paused"
    COMPLETED = "completed"
    REVOKED = "revoked"
    EXPIRED = "expired"
    DECLINED = "declined"


class HandoffType(str, Enum):
    TEMPORARY = "temporary"
    PERMANENT = "permanent"


class ResponsibilityType(str, Enum):
    DAILY_CARE = "daily_care"
    MEDICATION_MANAGEMENT = "medication_management"
    APPOINTMENTS_AND_TRANSPORT = "appointments_and_transport"
    HEALTH_MONITORING = "health_monitoring"
    DECISION_MAKING = "decision_making"
    MEDICAL_CONSENT = "medical_consent"
    FINANCIAL_MANAGEMENT = "financial_management"
    COMMUNICATION_LIAISON = "communication_liaison"


class PermissionScope(str, Enum):
    READ_ONLY = "read_only"
    READ_AND_ACT = "read_and_act"
    READ_ACT_AND_SHARE = "read_act_and_share"


class HandoffParticipantRole(str, Enum):
    SENDER = "sender"
    PRIMARY_RECIPIENT = "primary_recipient"
    SECONDARY_RECIPIENT = "secondary_recipient"
    WITNESS = "witness"
    SUPPORTER = "supporter"


class InvitationStatus(str, Enum):
    PENDING = "pending"
    ACCEPTED = "accepted"
    DECLINED = "declined"
    EXPIRED = "expired"
    WITHDRAWN = "withdrawn"


class QuestionStatus(str, Enum):
    ASKED = "asked"
    ANSWERED = "answered"
    WITHDRAWN = "withdrawn"


class AcknowledgmentType(str, Enum):
    READ = "read"
    UNDERSTOOD = "understood"
    ACCEPTED = "accepted"
    DECLINED = "declined"


class HandoffEventType(str, Enum):
    CREATED = "created"
    PROPOSED = "proposed"
    INVITED = "invited"
    INVITATION_ACCEPTED = "invitation_accepted"
    INVITATION_DECLINED = "invitation_declined"
    INVITATION_WITHDRAWN = "invitation_withdrawn"
    ACCEPTED = "accepted"
    DECLINED = "declined"
    ACTIVATED = "activated"
    PAUSED = "paused"
    RESUMED = "resumed"
    COMPLETED = "completed"
    REVOKED = "revoked"
    EXPIRED = "expired"
    QUESTION_ASKED = "question_asked"
    QUESTION_ANSWERED = "question_answered"
    QUESTION_WITHDRAWN = "question_withdrawn"
    ACKNOWLEDGMENT_RECORD = "acknowledgment_recorded"
    CONTEXT_SNAPSHOT_TAKEN = "context_snapshot_taken"
    PERMISSION_GRANTED = "permission_granted"
    PERMISSION_REVOKED = "permission_revoked"


class ContextSnapshotTrigger(str, Enum):
    HANDOFF_PROPOSAL = "handoff_proposal"
    ACCEPTANCE = "acceptance"
    ACTIVATION = "activation"
    PAUSE = "pause"
    RESUME = "resume"
    COMPLETION = "completion"
    MANUAL_REQUEST = "manual_request"


class ResponsibilityStatus(str, Enum):
    PROPOSED = "proposed"
    ACCEPTED = "accepted"
    ACTIVE = "active"
    COMPLETED = "completed"
    DECLINED = "declined"
    TRANSFERRED = "transferred"


class EvidenceLinkType(str, Enum):
    BASIS = "basis"
    CONTEXT = "context"
    REFERENCE = "reference"
    ACTION_ITEM = "action_item"


class HandoffAccessLevel(str, Enum):
    VIEW = "view"
    PARTICIPATE = "participate"
    MANAGE = "manage"
    ADMINISTER = "administer"


class HandoffPermission(str, Enum):
    VIEW_HANDOFF = "view_handoff"
    VIEW_CONTEXT = "view_context"
    VIEW_EVIDENCE = "view_evidence"
    VIEW_AUDIT_LOG = "view_audit_log"
    ACCEPT_HANDOFF = "accept_handoff"
    DECLINE_HANDOFF = "decline_handoff"
    ASK_QUESTIONS = "ask_questions"
    ANSWER_QUESTIONS = "answer_questions"
    ACKNOWLEDGE_HANDOFF = "acknowledge_handoff"
    MODIFY_HANDOFF = "modify_handoff"
    PAUSE_HANDOFF = "pause_handoff"
    RESUME_HANDOFF = "resume_handoff"
    COMPLETE_HANDOFF = "complete_handoff"
    REVOKE_HANDOFF = "revoke_handoff"
    INVITE_PARTICIPANTS = "invite_participants"
    VIEW_PARTICIPANTS = "view_participants"
    MANAGE_PERMISSIONS = "manage_permissions"
    TAKE_CONTEXT_SNAPSHOT = "take_context_snapshot"
    EXPORT_CONTEXT = "export_context"