import hashlib
import uuid
from sqlalchemy import Boolean, Column, DateTime, ForeignKey, String, Text, Integer, JSON, UniqueConstraint, UniqueConstraint
from sqlalchemy.sql import func

from app.core.database import Base


class CaregiverDevice(Base):
    __tablename__ = "caregiver_devices"
    __table_args__ = (UniqueConstraint("caregiver_id", "installation_key_hash", name="uq_caregiver_device_installation"),)
    __table_args__ = (UniqueConstraint("caregiver_id", "installation_key_hash", name="uq_caregiver_device_installation"),)
    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    account_id = Column(String(36), ForeignKey("caregiver_accounts.id", ondelete="SET NULL"), nullable=True, index=True)
    caregiver_id = Column(String(36), ForeignKey("caregivers.id", ondelete="CASCADE"), nullable=False, index=True)
    installation_key_hash = Column(String(64), nullable=False)
    label = Column(String(120), nullable=False, default="This device")
    platform = Column(String(40), nullable=True)
    last_seen_at = Column(DateTime(timezone=True), nullable=True)
    revoked_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class DeviceTrustRecord(Base):
    __tablename__ = "device_trust_records"
    __table_args__ = (UniqueConstraint("device_id", name="uq_device_trust_record_device"),)
    __table_args__ = (UniqueConstraint("device_id", name="uq_device_trust_record_device"),)
    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    device_id = Column(String(36), ForeignKey("caregiver_devices.id", ondelete="CASCADE"), nullable=False, index=True)
    trust_status = Column(String(24), nullable=False, default="unverified")
    verified_at = Column(DateTime(timezone=True), nullable=True)
    expires_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class DeviceEnrollmentEvent(Base):
    __tablename__ = "device_enrollment_events"
    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    device_id = Column(String(36), ForeignKey("caregiver_devices.id", ondelete="SET NULL"), nullable=True)
    caregiver_id = Column(String(36), ForeignKey("caregivers.id", ondelete="CASCADE"), nullable=False, index=True)
    event_type = Column(String(40), nullable=False)
    details = Column(JSON, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class DeviceRevocation(Base):
    __tablename__ = "device_revocations"
    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    device_id = Column(String(36), ForeignKey("caregiver_devices.id", ondelete="CASCADE"), nullable=False, index=True)
    caregiver_id = Column(String(36), ForeignKey("caregivers.id", ondelete="CASCADE"), nullable=False)
    reason = Column(String(120), nullable=True)
    revoked_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class AccountRecoveryRequest(Base):
    __tablename__ = "account_recovery_requests"
    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    account_id = Column(String(36), ForeignKey("caregiver_accounts.id", ondelete="SET NULL"), nullable=True, index=True)
    email_hash = Column(String(64), nullable=False)
    status = Column(String(24), nullable=False, default="pending_verification")
    requested_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    resolved_at = Column(DateTime(timezone=True), nullable=True)
    request_context_hash = Column(String(64), nullable=True)


class AccountRecoveryEvent(Base):
    __tablename__ = "account_recovery_events"
    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    recovery_request_id = Column(String(36), ForeignKey("account_recovery_requests.id", ondelete="SET NULL"), nullable=True, index=True)
    event_type = Column(String(40), nullable=False)
    actor_account_id = Column(String(36), ForeignKey("caregiver_accounts.id", ondelete="SET NULL"), nullable=True)
    details = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class AccountSecurityEvent(Base):
    __tablename__ = "account_security_events"
    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    account_id = Column(String(36), ForeignKey("caregiver_accounts.id", ondelete="SET NULL"), nullable=True, index=True)
    caregiver_id = Column(String(36), ForeignKey("caregivers.id", ondelete="SET NULL"), nullable=True, index=True)
    event_type = Column(String(60), nullable=False)
    details = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class IdentityAuditEvent(Base):
    __tablename__ = "identity_audit_events"
    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    actor_caregiver_id = Column(String(36), ForeignKey("caregivers.id", ondelete="SET NULL"), nullable=True, index=True)
    person_id = Column(String(36), ForeignKey("persons.id", ondelete="SET NULL"), nullable=True, index=True)
    action = Column(String(60), nullable=False)
    details = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class LoginAttemptLimit(Base):
    __tablename__ = "login_attempt_limits"
    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    key_hash = Column(String(64), nullable=False, unique=True)
    window_started_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    attempts = Column(Integer, nullable=False, default=0)
    blocked_until = Column(DateTime(timezone=True), nullable=True)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class LoginAttemptLimit(Base):
    __tablename__ = "login_attempt_limits"
    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    key_hash = Column(String(64), nullable=False, unique=True)
    window_started_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    attempts = Column(Integer, nullable=False, default=0)
    blocked_until = Column(DateTime(timezone=True), nullable=True)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
