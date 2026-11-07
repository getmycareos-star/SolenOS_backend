import uuid

from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Integer, JSON, String, Text, UniqueConstraint
from sqlalchemy.sql import func

from app.core.database import Base


def _id():
    return str(uuid.uuid4())


class HandoffRecord(Base):
    __tablename__ = "handoff_records"

    id = Column(String(36), primary_key=True, default=_id)
    actor_id = Column(String(255), nullable=True, index=True)
    subject_id = Column(String(255), nullable=True, index=True)
    person_id = Column(String(255), nullable=False, index=True)
    caregiver_id = Column(String(255), nullable=True, index=True)
    created_by = Column(String(255), nullable=False)
    handoff_status = Column(String(40), nullable=False, default="pending")
    scope_snapshot = Column(JSON, nullable=False, default=list)
    summary_snapshot = Column(JSON, nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now(), index=True)
    expires_at = Column(DateTime(timezone=True), nullable=True, index=True)
    revoked_at = Column(DateTime(timezone=True), nullable=True)
    summary_id = Column(String(255), nullable=True)
    request_id = Column(String(255), nullable=True, index=True)
    correlation_id = Column(String(255), nullable=True, index=True)
    policy_version = Column(String(100), nullable=True)
    record_metadata = Column("metadata", JSON, nullable=False, default=dict)
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())


class HandoffRecipient(Base):
    __tablename__ = "handoff_recipients"

    id = Column(String(36), primary_key=True, default=_id)
    actor_id = Column(String(255), nullable=True, index=True)
    subject_id = Column(String(255), nullable=True, index=True)
    person_id = Column(String(255), nullable=True, index=True)
    caregiver_id = Column(String(255), nullable=True, index=True)
    handoff_id = Column(String(36), nullable=False, index=True)
    recipient_id = Column(String(255), nullable=False, index=True)
    recipient_role = Column(String(80), nullable=True)
    access_status = Column(String(40), nullable=False, default="pending")
    invited_at = Column(DateTime(timezone=True), nullable=True)
    accepted_at = Column(DateTime(timezone=True), nullable=True)
    revoked_at = Column(DateTime(timezone=True), nullable=True)
    request_id = Column(String(255), nullable=True, index=True)
    correlation_id = Column(String(255), nullable=True, index=True)
    policy_version = Column(String(100), nullable=True)
    record_metadata = Column("metadata", JSON, nullable=False, default=dict)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())


class HandoffAcknowledgement(Base):
    __tablename__ = "handoff_acknowledgements"

    id = Column(String(36), primary_key=True, default=_id)
    actor_id = Column(String(255), nullable=True, index=True)
    subject_id = Column(String(255), nullable=True, index=True)
    person_id = Column(String(255), nullable=True, index=True)
    caregiver_id = Column(String(255), nullable=True, index=True)
    handoff_id = Column(String(36), nullable=False, index=True)
    recipient_id = Column(String(255), nullable=False, index=True)
    acknowledged_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    acknowledgement = Column(Text, nullable=False)
    note = Column(Text, nullable=True)
    request_id = Column(String(255), nullable=True, index=True)
    correlation_id = Column(String(255), nullable=True, index=True)
    policy_version = Column(String(100), nullable=True)
    record_metadata = Column("metadata", JSON, nullable=False, default=dict)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())


class HandoffAccessLog(Base):
    __tablename__ = "handoff_access_logs"

    id = Column(String(36), primary_key=True, default=_id)
    actor_id = Column(String(255), nullable=True, index=True)
    subject_id = Column(String(255), nullable=True, index=True)
    person_id = Column(String(255), nullable=True, index=True)
    caregiver_id = Column(String(255), nullable=True, index=True)
    handoff_id = Column(String(36), nullable=False, index=True)
    recipient_id = Column(String(255), nullable=True, index=True)
    action = Column(String(80), nullable=False)
    decision = Column(String(40), nullable=False)
    reason_code = Column(String(120), nullable=True)
    occurred_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now(), index=True)
    request_id = Column(String(255), nullable=True, index=True)
    correlation_id = Column(String(255), nullable=True, index=True)
    policy_version = Column(String(100), nullable=True)
    record_metadata = Column("metadata", JSON, nullable=False, default=dict)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())


class AuditLogEvent(Base):
    __tablename__ = "audit_log_events"

    id = Column(String(36), primary_key=True, default=_id)
    actor_id = Column(String(255), nullable=True, index=True)
    subject_id = Column(String(255), nullable=True, index=True)
    person_id = Column(String(255), nullable=True, index=True)
    caregiver_id = Column(String(255), nullable=True, index=True)
    action = Column(String(120), nullable=False)
    target_type = Column(String(120), nullable=False)
    target_id = Column(String(255), nullable=True)
    occurred_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now(), index=True)
    outcome = Column(String(40), nullable=False)
    request_id = Column(String(255), nullable=True, index=True)
    correlation_id = Column(String(255), nullable=True, index=True)
    policy_version = Column(String(100), nullable=True)
    event_hash = Column(String(128), nullable=True)
    previous_event_hash = Column(String(128), nullable=True)
    record_metadata = Column("metadata", JSON, nullable=False, default=dict)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())



class NotificationReadReceipt(Base):
    __tablename__ = "notification_read_receipts"
    __table_args__ = (
        UniqueConstraint("notification_id", "reader_id", name="uq_notification_read_receipt_reader"),
    )

    id = Column(String(36), primary_key=True, default=_id)
    actor_id = Column(String(255), nullable=True, index=True)
    subject_id = Column(String(255), nullable=True, index=True)
    person_id = Column(String(255), nullable=True, index=True)
    caregiver_id = Column(String(255), nullable=True, index=True)
    notification_id = Column(String(255), nullable=False, index=True)
    reader_id = Column(String(255), nullable=False, index=True)
    read_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now(), index=True)
    client = Column(String(255), nullable=True)
    request_id = Column(String(255), nullable=True, index=True)
    correlation_id = Column(String(255), nullable=True, index=True)
    policy_version = Column(String(100), nullable=True)
    record_metadata = Column("metadata", JSON, nullable=False, default=dict)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())



class HandoffDraft(Base):
    """Caregiver-owned, versioned handoff draft; never a shared HandoffRecord."""
    __tablename__ = "handoff_drafts"
    __table_args__ = (
        UniqueConstraint("person_id", "caregiver_id", "request_id", name="uq_handoff_drafts_request"),
    )

    id = Column(String(36), primary_key=True, default=_id)
    person_id = Column(String(255), nullable=False, index=True)
    caregiver_id = Column(String(255), nullable=False, index=True)
    request_id = Column(String(255), nullable=False)
    request_fingerprint = Column(String(64), nullable=False)
    purpose = Column(String(64), nullable=False)
    audience = Column(String(32), nullable=False)
    window_days = Column(Integer, nullable=False)
    structured_content = Column(JSON, nullable=False, default=dict)
    source_snapshot = Column(JSON, nullable=False, default=dict)
    generation_metadata = Column(JSON, nullable=False, default=dict)
    generation_status = Column(String(48), nullable=False, default="not_generated")
    validation_status = Column(String(48), nullable=False, default="not_assessed")
    review_status = Column(String(48), nullable=False, default="pending_review")
    revision_number = Column(Integer, nullable=False, default=1)
    approved_at = Column(DateTime(timezone=True), nullable=True)
    approved_by_caregiver_id = Column(String(255), nullable=True)
    approval_revision = Column(Integer, nullable=True)
    approval_content_sha256 = Column(String(64), nullable=True)
    approval_parameters = Column(JSON, nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now(), index=True)
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())


class HandoffDraftRevision(Base):
    """Append-only content/source snapshot for every saved caregiver draft revision."""
    __tablename__ = "handoff_draft_revisions"
    __table_args__ = (
        UniqueConstraint("draft_id", "revision_number", name="uq_handoff_draft_revision_number"),
    )

    id = Column(String(36), primary_key=True, default=_id)
    draft_id = Column(String(36), ForeignKey("handoff_drafts.id", ondelete="CASCADE"), nullable=False, index=True)
    person_id = Column(String(255), nullable=False, index=True)
    caregiver_id = Column(String(255), nullable=False, index=True)
    revision_number = Column(Integer, nullable=False)
    content = Column(JSON, nullable=False)
    source_snapshot = Column(JSON, nullable=False)
    content_sha256 = Column(String(64), nullable=False)
    change_type = Column(String(32), nullable=False)
    created_by_caregiver_id = Column(String(255), nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now(), index=True)
