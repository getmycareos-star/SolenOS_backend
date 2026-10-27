"""Add privacy, sharing, audit, notification, reminder and handoff lifecycle records.

Revision ID: 20261027_03_privacy_delivery
Revises: 20261027_06_ai_output_governance

Additive and idempotent: existing tables are preserved. Domain-specific payloads are
stored in JSON while frequently queried lifecycle/security fields remain typed.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "20261027_03_privacy_delivery"
down_revision = "20261027_06_ai_output_governance"
branch_labels = None
depends_on = None

# These are lifecycle/event tables, not replacements for existing care-domain tables.
# The common columns make actor, target, outcome, request correlation and policy
# version queryable without putting secrets or unnecessary care details in logs.
TABLES = {
    # Shared summary controls
    "shared_summary_expirations": ["summary_id", "expires_at", "policy", "status"],
    "shared_summary_revocations": ["summary_id", "revoked_by", "revoked_at", "reason"],
    "shared_summary_access_logs": ["summary_id", "access_type", "decision", "denial_reason", "ip_fingerprint", "user_agent"],
    "shared_summary_downloads": ["summary_id", "summary_version_id", "downloaded_at", "result"],
    "shared_summary_generation_jobs": ["summary_id", "job_status", "started_at", "finished_at", "failure_code", "attempt_count"],
    "shared_summary_delivery_receipts": ["summary_id", "recipient_id", "delivery_channel", "delivery_status", "attempted_at", "confirmed_at", "provider_reference"],
    "shared_summary_consent_records": ["summary_id", "subject_id", "consent_status", "consent_version", "consented_at", "withdrawn_at"],
    "shared_summary_disclosure_records": ["summary_id", "summary_version_id", "recipient_id", "scope_snapshot", "disclosed_at", "purpose"],
    "shared_summary_link_events": ["summary_id", "event_type", "occurred_at", "token_fingerprint"],
    # Privacy rights and incident handling
    "data_deletion_requests": ["requester_id", "subject_id", "request_status", "requested_at", "completed_at", "reason_code"],
    "data_deletion_jobs": ["request_id", "job_status", "started_at", "finished_at", "verified_at", "verification_result", "failure_code"],
    "data_export_requests": ["requester_id", "subject_id", "request_status", "requested_at", "completed_at", "artifact_reference", "expires_at"],
    "privacy_incidents": ["incident_status", "severity", "detected_at", "closed_at", "incident_type", "owner_id"],
    "privacy_incident_events": ["incident_id", "event_type", "occurred_at", "actor_id", "notes_redacted"],
    "data_processing_records": ["processing_purpose", "data_categories", "legal_basis", "retention_policy", "active_from", "active_until"],
    "data_subject_requests": ["requester_id", "subject_id", "request_type", "request_status", "requested_at", "resolved_at", "resolution_code"],
    "sensitive_data_access_logs": ["subject_id", "resource_type", "resource_id", "access_type", "decision", "denial_reason"],
    "legal_holds": ["subject_id", "resource_type", "resource_id", "hold_status", "reason_code", "effective_at", "released_at", "authorized_by"],
    # Audit and authorization
    "audit_log_events": ["actor_id", "action", "target_type", "target_id", "occurred_at", "outcome", "request_id", "correlation_id", "policy_version", "event_hash", "previous_event_hash"],
    "audit_log_integrity_checks": ["checked_at", "range_start", "range_end", "check_status", "checked_count", "failure_count", "details"],
    "audit_log_access_records": ["actor_id", "access_type", "decision", "reason_code", "occurred_at"],
    "privileged_access_logs": ["actor_id", "privilege", "action", "target_type", "target_id", "occurred_at", "outcome", "reason_code"],
    "authorization_decisions": ["actor_id", "resource_type", "resource_id", "action", "decision", "reason_code", "policy_version", "decided_at"],
    "failed_access_attempts": ["actor_id", "resource_type", "resource_id", "action", "reason_code", "attempted_at", "ip_fingerprint"],
    # Notifications and reminders
    "care_notifications": ["person_id", "caregiver_id", "title", "body", "category", "priority", "source_event_id", "created_at", "read_at"],
    "care_notification_preferences": ["person_id", "caregiver_id", "quiet_hours_enabled", "reminders_enabled", "updated_at"],
    "notification_delivery_attempts": ["notification_id", "attempt_number", "channel", "attempted_at", "result", "failure_code", "provider_reference"],
    "notification_delivery_receipts": ["notification_id", "provider_reference", "receipt_status", "received_at", "details"],
    "notification_read_receipts": ["notification_id", "reader_id", "read_at", "client"],
    "care_reminders": ["person_id", "created_by_caregiver_id", "title", "note", "due_at", "status", "completed_at", "completed_by_caregiver_id", "completion_note", "cancelled_at", "cancelled_by_caregiver_id"],
    "reminder_acknowledgements": ["reminder_id", "acknowledged_by", "acknowledged_at", "acknowledgement", "note"],
    "reminder_escalations": ["reminder_id", "recipient_id", "escalation_level", "reason_code", "escalated_at", "resolved_at", "status"],
    # Care handoffs
    "handoff_records": ["person_id", "created_by", "handoff_status", "scope_snapshot", "created_at", "expires_at", "revoked_at", "summary_id"],
    "handoff_recipients": ["handoff_id", "recipient_id", "recipient_role", "access_status", "invited_at", "accepted_at", "revoked_at"],
    "handoff_access_logs": ["handoff_id", "recipient_id", "action", "decision", "reason_code", "occurred_at", "request_id"],
    "handoff_acknowledgements": ["handoff_id", "recipient_id", "acknowledged_at", "acknowledgement", "note"],
    "delivery_failure_events": ["delivery_type", "delivery_id", "recipient_id", "failure_code", "failure_detail_redacted", "occurred_at", "retryable", "resolved_at"],
}

REQUIRED_COLUMNS = {
    "shared_summary_expirations": {"summary_id", "expires_at", "status"},
    "shared_summary_revocations": {"summary_id", "revoked_by", "revoked_at"},
    "shared_summary_access_logs": {"summary_id", "access_type", "decision"},
    "shared_summary_downloads": {"summary_id", "downloaded_at", "result"},
    "shared_summary_generation_jobs": {"summary_id", "job_status"},
    "shared_summary_delivery_receipts": {"summary_id", "recipient_id", "delivery_status"},
    "shared_summary_consent_records": {"summary_id", "subject_id", "consent_status", "consent_version"},
    "shared_summary_disclosure_records": {"summary_id", "recipient_id", "scope_snapshot", "disclosed_at"},
    "shared_summary_link_events": {"summary_id", "event_type", "occurred_at"},
    "data_deletion_requests": {"requester_id", "subject_id", "request_status", "requested_at"},
    "data_deletion_jobs": {"request_id", "job_status"},
    "data_export_requests": {"requester_id", "subject_id", "request_status", "requested_at"},
    "privacy_incidents": {"incident_status", "severity", "detected_at"},
    "privacy_incident_events": {"incident_id", "event_type", "occurred_at"},
    "data_processing_records": {"processing_purpose", "data_categories", "legal_basis"},
    "data_subject_requests": {"requester_id", "subject_id", "request_type", "request_status", "requested_at"},
    "sensitive_data_access_logs": {"subject_id", "resource_type", "resource_id", "access_type", "decision"},
    "legal_holds": {"subject_id", "resource_type", "resource_id", "hold_status"},
    "audit_log_events": {"action", "target_type", "occurred_at", "outcome"},
    "audit_log_integrity_checks": {"checked_at", "check_status"},
    "audit_log_access_records": {"actor_id", "access_type", "decision", "occurred_at"},
    "privileged_access_logs": {"actor_id", "privilege", "action", "occurred_at", "outcome"},
    "authorization_decisions": {"actor_id", "resource_type", "resource_id", "action", "decision", "decided_at"},
    "failed_access_attempts": {"action", "reason_code", "attempted_at"},
    "care_notifications": {"person_id", "caregiver_id", "title", "body"},
    "care_notification_preferences": {"person_id", "caregiver_id", "quiet_hours_enabled", "reminders_enabled"},
    "notification_delivery_attempts": {"notification_id", "attempt_number", "channel", "attempted_at", "result"},
    "notification_delivery_receipts": {"notification_id", "receipt_status", "received_at"},
    "notification_read_receipts": {"notification_id", "reader_id", "read_at"},
    "reminder_acknowledgements": {"reminder_id", "acknowledged_by", "acknowledged_at", "acknowledgement"},
    "reminder_escalations": {"reminder_id", "recipient_id", "escalation_level", "escalated_at", "status"},
    "handoff_records": {"person_id", "created_by", "handoff_status", "scope_snapshot"},
    "handoff_recipients": {"handoff_id", "recipient_id", "access_status"},
    "handoff_access_logs": {"handoff_id", "action", "decision", "occurred_at"},
    "handoff_acknowledgements": {"handoff_id", "recipient_id", "acknowledged_at", "acknowledgement"},
    "delivery_failure_events": {"delivery_type", "delivery_id", "failure_code", "occurred_at", "retryable"},
}

def _column_type(name):
    if name.endswith(("_at", "_from", "_until", "_start", "_end")) or name in ("expires_at", "scheduled_at", "sent_at", "read_at", "due_at"):
        return sa.DateTime(timezone=True)
    if name in ("scope_snapshot", "data_categories", "quiet_hours", "details", "verification_result"):
        return sa.JSON()
    if name in ("enabled", "retryable") or name.endswith("_enabled"):
        return sa.Boolean()
    if name in ("attempt_number", "attempt_count", "escalation_level", "checked_count", "failure_count"):
        return sa.Integer()
    if name in ("id", "actor_id", "subject_id", "person_id", "caregiver_id", "request_id", "correlation_id", "policy_version"):
        return sa.String(255)
    return sa.Text()


def _column_definition(name, table_name=None):
    if name == "id":
        return sa.Column(name, sa.String(36), primary_key=True, nullable=False)
    if name == "metadata":
        return sa.Column(name, sa.JSON(), nullable=False, server_default=sa.text("'{}'"))
    if name == "quiet_hours_enabled":
        return sa.Column(name, sa.Boolean(), nullable=False, server_default=sa.false())
    if name == "reminders_enabled":
        return sa.Column(name, sa.Boolean(), nullable=False, server_default=sa.true())
    if name in ("created_at", "updated_at"):
        return sa.Column(name, sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now())
    if table_name and name in REQUIRED_COLUMNS.get(table_name, set()):
        return sa.Column(name, _column_type(name), nullable=False)
    return sa.Column(name, _column_type(name), nullable=True)


def upgrade():
    bind = op.get_bind()
    inspector = inspect(bind)
    envelope = ["id", "actor_id", "subject_id", "person_id", "caregiver_id"]
    shared = ["request_id", "correlation_id", "policy_version", "metadata", "created_at", "updated_at"]

    for table_name, extra_columns in TABLES.items():
        required = list(dict.fromkeys(envelope + extra_columns + shared))
        if not inspector.has_table(table_name):
            op.create_table(table_name, *[_column_definition(name, table_name) for name in required])
            inspector = inspect(bind)
        else:
            # Reconcile an existing table in place instead of skipping it wholesale.
            # Never rebuild or drop existing data. Add only missing nullable/defaulted columns.
            existing = {col["name"] for col in inspector.get_columns(table_name)}
            for name in required:
                if name in existing:
                    continue
                definition = _column_definition(name, table_name)
                # Existing tables receive nullable columns only; never impose a new
                # primary-key constraint over populated legacy data.
                op.add_column(table_name, sa.Column(name, definition.type, nullable=True))
                existing.add(name)
            inspector = inspect(bind)

        existing_indexes = {idx["name"] for idx in inspector.get_indexes(table_name)}
        index_columns = ["created_at", "actor_id", "subject_id", "person_id", "caregiver_id", "request_id", "correlation_id"]
        if any(x in table_name for x in ("logs", "events", "receipts", "attempts", "acknowledgements", "revocations", "downloads")):
            index_columns += ["occurred_at", "attempted_at", "acknowledged_at", "revoked_at", "downloaded_at", "disclosed_at", "received_at", "read_at"]
        actual_columns = {col["name"] for col in inspector.get_columns(table_name)}
        for col in dict.fromkeys(index_columns):
            if col not in actual_columns:
                continue
            index_name = f"ix_{table_name}_{col}"
            if index_name not in existing_indexes:
                op.create_index(index_name, table_name, [col])
                existing_indexes.add(index_name)
        inspector = inspect(bind)

def downgrade():
    # Intentionally non-destructive. Some tables may have pre-existed and were
    # skipped by upgrade; dropping by name could destroy user data.
    pass
