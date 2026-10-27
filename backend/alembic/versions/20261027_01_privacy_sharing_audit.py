"""Add privacy, sharing, audit, notification, reminder and handoff lifecycle records.

Revision ID: 20261027_03_privacy_delivery
Revises: 20261027_02_continuity_lifecycle

Additive and idempotent: existing tables are preserved. Domain-specific payloads are
stored in JSON while frequently queried lifecycle/security fields remain typed.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "20261027_03_privacy_delivery"
down_revision = "20261027_02_continuity_lifecycle"
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
    "notifications": ["recipient_id", "person_id", "notification_type", "title", "body", "status", "scheduled_at", "sent_at", "read_at"],
    "notification_preferences": ["recipient_id", "channel", "enabled", "quiet_hours", "updated_at"],
    "notification_delivery_attempts": ["notification_id", "attempt_number", "channel", "attempted_at", "result", "failure_code", "provider_reference"],
    "notification_delivery_receipts": ["notification_id", "provider_reference", "receipt_status", "received_at", "details"],
    "notification_read_receipts": ["notification_id", "reader_id", "read_at", "client"],
    "reminder_acknowledgements": ["reminder_id", "acknowledged_by", "acknowledged_at", "acknowledgement", "note"],
    "reminder_escalations": ["reminder_id", "recipient_id", "escalation_level", "reason_code", "escalated_at", "resolved_at", "status"],
    # Care handoffs
    "handoff_records": ["person_id", "created_by", "handoff_status", "scope_snapshot", "created_at", "expires_at", "revoked_at", "summary_id"],
    "handoff_recipients": ["handoff_id", "recipient_id", "recipient_role", "access_status", "invited_at", "accepted_at", "revoked_at"],
    "handoff_access_logs": ["handoff_id", "recipient_id", "action", "decision", "reason_code", "occurred_at", "request_id"],
    "handoff_acknowledgements": ["handoff_id", "recipient_id", "acknowledged_at", "acknowledgement", "note"],
    "delivery_failure_events": ["delivery_type", "delivery_id", "recipient_id", "failure_code", "failure_detail_redacted", "occurred_at", "retryable", "resolved_at"],
}

def upgrade():
    bind = op.get_bind()
    inspector = inspect(bind)
    for table_name, extra_columns in TABLES.items():
        if inspector.has_table(table_name):
            continue
        columns = [
            sa.Column("id", sa.String(36), primary_key=True, nullable=False),
            sa.Column("actor_id", sa.String(255), nullable=True),
            sa.Column("subject_id", sa.String(255), nullable=True),
            sa.Column("person_id", sa.String(255), nullable=True),
            sa.Column("caregiver_id", sa.String(255), nullable=True),
        ]
        existing_names = {column.name for column in columns}
        for name in extra_columns:
            # Avoid duplicate definitions where a domain field is already provided
            # by the shared event envelope (actor_id, subject_id, person_id, etc.).
            if name in existing_names:
                continue
            # Known timestamp fields are typed timestamps; JSON fields preserve exact
            # disclosure/scope snapshots without duplicating care facts into audit logs.
            if name.endswith(("_at", "_from", "_until", "_start", "_end")) or name in ("expires_at", "scheduled_at", "sent_at", "read_at", "due_at"):
                col_type = sa.DateTime(timezone=True)
            elif name in ("scope_snapshot", "data_categories", "quiet_hours", "details", "verification_result"):
                col_type = sa.JSON()
            elif name in ("enabled", "retryable"):
                col_type = sa.Boolean()
            elif name in ("attempt_number", "attempt_count", "escalation_level", "checked_count", "failure_count"):
                col_type = sa.Integer()
            else:
                col_type = sa.Text()
            columns.append(sa.Column(name, col_type, nullable=True))
            existing_names.add(name)
        for name, column_type, kwargs in [
            ("request_id", sa.String(255), {"nullable": True}),
            ("correlation_id", sa.String(255), {"nullable": True}),
            ("policy_version", sa.String(100), {"nullable": True}),
            ("metadata", sa.JSON(), {"nullable": False, "server_default": sa.text("'{}'")}),
            ("created_at", sa.DateTime(timezone=True), {"nullable": False, "server_default": sa.func.now()}),
            ("updated_at", sa.DateTime(timezone=True), {"nullable": False, "server_default": sa.func.now()}),
        ]:
            if name not in existing_names:
                columns.append(sa.Column(name, column_type, **kwargs))
                existing_names.add(name)
        op.create_table(table_name, *columns)
        op.create_index(f"ix_{table_name}_created_at", table_name, ["created_at"])
        for col in ("actor_id", "subject_id", "person_id", "caregiver_id", "request_id", "correlation_id"):
            if col in [c.name for c in columns]:
                op.create_index(f"ix_{table_name}_{col}", table_name, [col])
        # Every audit/event stream should support time-ordered review.
        if any(x in table_name for x in ("logs", "events", "receipts", "attempts", "acknowledgements", "revocations", "downloads")):
            # created_at already has an index above; do not create it twice.
            for candidate in ("occurred_at", "attempted_at", "acknowledged_at", "revoked_at", "downloaded_at", "disclosed_at", "received_at", "read_at"):
                if candidate in [c.name for c in columns]:
                    op.create_index(f"ix_{table_name}_{candidate}", table_name, [candidate])
                    break

def downgrade():
    # Intentionally non-destructive. Some tables may have pre-existed and were
    # skipped by upgrade; dropping by name could destroy user data.
    pass
