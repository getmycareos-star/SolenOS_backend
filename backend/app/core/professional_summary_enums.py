"""Enums for the Professional Summary capability.

The lifecycle here is deliberately explicit. The four transitions that matter
most are all separate gates, and collapsing any of them is how a summary ends up
in a clinician's inbox without anyone having actually checked it:

  generated  != approved   (someone reviewed it)
  approved   != shared     (someone authorized sending it)
  shared     != received   (the professional actually got it)
  received   != responded  (the professional actually said something)

Nothing is auto-advanced. Every state change is an explicit action recorded with
who did it and when.
"""

from enum import Enum


class ProfessionalSummaryStatus(str, Enum):
    """Lifecycle states for a Professional Summary."""

    DRAFT = "draft"
    """Authoring in progress. Not visible to anyone else."""

    GENERATED = "generated"
    """Synthesized from CareContext. Awaiting caregiver review."""

    UNDER_REVIEW = "under_review"
    """Caregiver is reading it. May still be corrected."""

    CORRECTION_REQUIRED = "correction_required"
    """Caregiver found something wrong. Awaiting regeneration or re-approval."""

    APPROVED = "approved"
    """Caregiver has verified the content. Eligible for sharing."""

    SHARED = "shared"
    """Sent to an authorized professional recipient."""

    OPENED = "opened"
    """Recipient has accessed the shared summary."""

    RESPONSE_RECEIVED = "response_received"
    """Professional has responded. Response captured as evidence."""

    SUPERSEDED = "superseded"
    """A newer version has been approved and shared. This one is historical."""

    REVOKED = "revoked"
    """Access withdrawn after sharing. Content remains historically identifiable."""


class SummarySection(str, Enum):
    """The substantive sections a professional summary is composed of.

    Each section carries its own evidence references and its own
    observed/reported/inferred classification. A section with no supporting
    evidence is reported as not established rather than omitted.
    """

    CURRENT_STATE = "current_state"
    INTERVAL_HISTORY = "interval_history"
    SINCE_LAST_VISIT = "since_last_visit"
    LONGITUDINAL_HISTORY = "longitudinal_history"
    RECENT_CHANGES = "recent_changes"
    MEDICATION_CHANGES = "medication_changes"
    CAREGIVER_OBSERVATIONS = "caregiver_observations"
    UNRESOLVED_QUESTIONS = "unresolved_questions"
    DECISIONS = "decisions"
    OUTCOMES = "outcomes"
    UNCERTAINTY = "uncertainty"
    CONFLICTS = "conflicts"
    EVIDENCE_REFERENCES = "evidence_references"


class StatementClassification(str, Enum):
    """How a claim in the summary is supported.

    These three are not interchangeable. "Caregiver reports X" is not the same
    as "the record shows X", and both are different from "the pattern may
    represent a change from baseline". Collapsing them is how a summary starts
    asserting things nobody actually established.
    """

    OBSERVED = "observed"
    """Directly recorded observation or measurement in the source record."""

    REPORTED = "reported"
    """Stated by a caregiver or other source, not independently verified."""

    INFERRED = "inferred"
    """System-generated interpretation derived from evidence.

    Always labelled as an interpretation, never presented as a fact.
    """

    UNKNOWN = "unknown"
    """Insufficient evidence. Reported as a gap, not filled in."""


class ResponseClassification(str, Enum):
    """How a professional response was classified for decision capture."""

    AMBIGUOUS = "ambiguous"
    """Could not be classified as a decision. Stored as a professional response."""

    CLARIFICATION = "clarification"
    COMMENT = "comment"
    RECOMMENDATION = "recommendation"
    DECISION = "decision"
    FOLLOW_UP_REQUEST = "follow_up_request"
    ADDITIONAL_INFORMATION = "additional_information"
    ACKNOWLEDGMENT = "acknowledgment"


class ShareRecipientType(str, Enum):
    """Who a summary can be shared with."""

    VERIFIED_PROFESSIONAL = "verified_professional"
    """A verified professional with an active role link for this person."""

    UNVERIFIED_PROFESSIONAL = "unverified_professional"
    """A professional identity that has not completed verification."""

    EXTERNAL_LINK = "external_link"
    """A shareable link. Requires authorization to generate and is revocable."""

    CIRCLE_CAREGIVER = "circle_caregiver"
    """Another authorized caregiver in the care circle."""


class AuditAction(str, Enum):
    """Actions recorded in the audit trail for the Professional Summary workflow."""

    SUMMARY_GENERATED = "summary_generated"
    SUMMARY_REVIEWED = "summary_reviewed"
    SUMMARY_CORRECTED = "summary_corrected"
    SUMMARY_APPROVED = "summary_approved"
    SUMMARY_SHARED = "summary_shared"
    SUMMARY_OPENED = "summary_opened"
    RESPONSE_RECEIVED = "response_received"
    EVIDENCE_CREATED = "evidence_created"
    DECISION_CAPTURED = "decision_captured"
    SUMMARY_SUPERSEDED = "summary_superseded"
    SUMMARY_REVOKED = "summary_revoked"
    ACCESS_DENIED = "access_denied"
    SUMMARY_REGENERATED = "summary_regenerated"