"""Identity, device trust, recovery and security audit foundation.

Revision ID: 20261026_03_identity_security
Revises: 20261026_02_improve_examples

Creates only missing identity-security tables. Existing care, account, consent,
and anonymous-session tables are preserved and are not recreated.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect
import uuid

revision = "20261026_03_identity_security"
down_revision = "20261026_02_improve_examples"
branch_labels = None
depends_on = None

NOW = sa.func.now()
JSON = sa.JSON()


def _fk(name, target, ondelete="CASCADE"):
    return sa.ForeignKey(target, name=name, ondelete=ondelete)


def _create(bind, name, columns, indexes=()):
    inspector = inspect(bind)
    if inspector.has_table(name):
        return
    op.create_table(name, sa.Column("id", sa.String(36), primary_key=True, nullable=False), *columns)
    for index_name, index_columns, unique in indexes:
        op.create_index(index_name, name, index_columns, unique=unique)


def upgrade():
    bind = op.get_bind()
    s = sa.String
    dt = sa.DateTime(timezone=True)
    txt = sa.Text
    integer = sa.Integer
    boolean = sa.Boolean
    json = sa.JSON

    # Identity links and auditable claims/merges.
    _create(bind, "caregiver_person_relationships", [
        sa.Column("caregiver_id", s(36), _fk("fk_cpr_caregiver", "caregivers.id"), nullable=False),
        sa.Column("person_id", s(36), _fk("fk_cpr_person", "persons.id"), nullable=False),
        sa.Column("relationship", s(80), nullable=False, server_default="Family"),
        sa.Column("status", s(24), nullable=False, server_default="active"),
        sa.Column("created_at", dt, nullable=False, server_default=NOW),
        sa.Column("updated_at", dt, nullable=True),
    ], [("ix_cpr_caregiver_id", ["caregiver_id"], False), ("ix_cpr_person_id", ["person_id"], False)])
    _create(bind, "caregiver_account_links", [
        sa.Column("account_id", s(36), _fk("fk_cal_account", "caregiver_accounts.id"), nullable=False),
        sa.Column("caregiver_id", s(36), _fk("fk_cal_caregiver", "caregivers.id"), nullable=False),
        sa.Column("person_id", s(36), _fk("fk_cal_person", "persons.id"), nullable=False),
        sa.Column("status", s(24), nullable=False, server_default="active"),
        sa.Column("created_at", dt, nullable=False, server_default=NOW),
    ], [("uq_cal_account_caregiver_person", ["account_id", "caregiver_id", "person_id"], True)])
    _create(bind, "caregiver_identity_verifications", [
        sa.Column("caregiver_id", s(36), _fk("fk_civ_caregiver", "caregivers.id"), nullable=False),
        sa.Column("method", s(40), nullable=False),
        sa.Column("status", s(24), nullable=False, server_default="pending"),
        sa.Column("verified_at", dt, nullable=True),
        sa.Column("reviewed_by", s(36), nullable=True),
        sa.Column("created_at", dt, nullable=False, server_default=NOW),
    ], [("ix_civ_caregiver_id", ["caregiver_id"], False)])
    _create(bind, "care_network_relationships", [
        sa.Column("person_id", s(36), _fk("fk_cnr_person", "persons.id"), nullable=False),
        sa.Column("caregiver_id", s(36), _fk("fk_cnr_caregiver", "caregivers.id"), nullable=False),
        sa.Column("related_caregiver_id", s(36), _fk("fk_cnr_related_caregiver", "caregivers.id"), nullable=True),
        sa.Column("relationship", s(80), nullable=False),
        sa.Column("status", s(24), nullable=False, server_default="pending"),
        sa.Column("created_at", dt, nullable=False, server_default=NOW),
    ], [("ix_cnr_person_id", ["person_id"], False), ("ix_cnr_caregiver_id", ["caregiver_id"], False)])
    _create(bind, "anonymous_identity_claims", [
        sa.Column("anonymous_session_id", s(36), _fk("fk_aic_session", "anonymous_sessions.id"), nullable=False),
        sa.Column("account_id", s(36), _fk("fk_aic_account", "caregiver_accounts.id"), nullable=True),
        sa.Column("challenge_hash", s(64), nullable=True),
        sa.Column("status", s(24), nullable=False, server_default="pending"),
        sa.Column("expires_at", dt, nullable=True),
        sa.Column("resolved_at", dt, nullable=True),
        sa.Column("created_at", dt, nullable=False, server_default=NOW),
    ], [("ix_aic_session_id", ["anonymous_session_id"], False), ("ix_aic_account_id", ["account_id"], False)])
    _create(bind, "anonymous_session_transfers", [
        sa.Column("source_session_id", s(36), _fk("fk_ast_source", "anonymous_sessions.id"), nullable=False),
        sa.Column("destination_session_id", s(36), _fk("fk_ast_destination", "anonymous_sessions.id", "SET NULL"), nullable=True),
        sa.Column("status", s(24), nullable=False, server_default="pending"),
        sa.Column("reason", txt, nullable=True),
        sa.Column("created_at", dt, nullable=False, server_default=NOW),
        sa.Column("completed_at", dt, nullable=True),
    ], [("ix_ast_source_session", ["source_session_id"], False)])
    _create(bind, "identity_merge_requests", [
        sa.Column("source_person_id", s(36), _fk("fk_imr_source", "persons.id"), nullable=False),
        sa.Column("target_person_id", s(36), _fk("fk_imr_target", "persons.id"), nullable=False),
        sa.Column("requested_by_caregiver_id", s(36), _fk("fk_imr_caregiver", "caregivers.id"), nullable=False),
        sa.Column("status", s(24), nullable=False, server_default="pending_review"),
        sa.Column("reason", txt, nullable=True),
        sa.Column("created_at", dt, nullable=False, server_default=NOW),
        sa.Column("resolved_at", dt, nullable=True),
    ], [("ix_imr_status", ["status"], False)])
    _create(bind, "identity_merge_audit", [
        sa.Column("source_person_id", s(36), nullable=False),
        sa.Column("target_person_id", s(36), nullable=False),
        sa.Column("actor_caregiver_id", s(36), nullable=True),
        sa.Column("action", s(40), nullable=False),
        sa.Column("details", json, nullable=True),
        sa.Column("created_at", dt, nullable=False, server_default=NOW),
    ])

    # Devices are registered as unverified; device possession never grants identity.
    _create(bind, "caregiver_devices", [
        sa.Column("account_id", s(36), _fk("fk_cd_account", "caregiver_accounts.id", "SET NULL"), nullable=True),
        sa.Column("caregiver_id", s(36), _fk("fk_cd_caregiver", "caregivers.id"), nullable=False),
        sa.Column("installation_key_hash", s(64), nullable=False),
        sa.Column("label", s(120), nullable=False, server_default="This device"),
        sa.Column("platform", s(40), nullable=True),
        sa.Column("last_seen_at", dt, nullable=True),
        sa.Column("revoked_at", dt, nullable=True),
        sa.Column("created_at", dt, nullable=False, server_default=NOW),
        sa.UniqueConstraint("caregiver_id", "installation_key_hash", name="uq_caregiver_device_installation"),
    ], [("ix_cd_caregiver_id", ["caregiver_id"], False), ("ix_cd_account_id", ["account_id"], False)])
    _create(bind, "device_trust_records", [
        sa.Column("device_id", s(36), _fk("fk_dtr_device", "caregiver_devices.id"), nullable=False),
        sa.Column("trust_status", s(24), nullable=False, server_default="unverified"),
        sa.Column("verified_at", dt, nullable=True),
        sa.Column("expires_at", dt, nullable=True),
        sa.Column("created_at", dt, nullable=False, server_default=NOW),
        sa.UniqueConstraint("device_id", name="uq_device_trust_record_device"),
    ], [("ix_dtr_device_id", ["device_id"], False)])
    _create(bind, "device_verification_challenges", [
        sa.Column("device_id", s(36), _fk("fk_dvc_device", "caregiver_devices.id"), nullable=False),
        sa.Column("challenge_hash", s(64), nullable=False),
        sa.Column("expires_at", dt, nullable=False),
        sa.Column("consumed_at", dt, nullable=True),
        sa.Column("attempt_count", integer, nullable=False, server_default="0"),
        sa.Column("created_at", dt, nullable=False, server_default=NOW),
    ], [("ix_dvc_device_id", ["device_id"], False)])
    _create(bind, "device_enrollment_events", [
        sa.Column("device_id", s(36), _fk("fk_dee_device", "caregiver_devices.id", "SET NULL"), nullable=True),
        sa.Column("caregiver_id", s(36), _fk("fk_dee_caregiver", "caregivers.id"), nullable=False),
        sa.Column("event_type", s(40), nullable=False),
        sa.Column("details", json, nullable=True),
        sa.Column("created_at", dt, nullable=False, server_default=NOW),
    ], [("ix_dee_caregiver_id", ["caregiver_id"], False)])
    _create(bind, "device_revocations", [
        sa.Column("device_id", s(36), _fk("fk_drv_device", "caregiver_devices.id"), nullable=False),
        sa.Column("caregiver_id", s(36), _fk("fk_drv_caregiver", "caregivers.id"), nullable=False),
        sa.Column("reason", s(120), nullable=True),
        sa.Column("revoked_at", dt, nullable=False, server_default=NOW),
    ], [("ix_drv_device_id", ["device_id"], False)])
    _create(bind, "device_recovery_requests", [
        sa.Column("account_id", s(36), _fk("fk_drr_account", "caregiver_accounts.id", "SET NULL"), nullable=True),
        sa.Column("caregiver_id", s(36), _fk("fk_drr_caregiver", "caregivers.id", "SET NULL"), nullable=True),
        sa.Column("device_id", s(36), _fk("fk_drr_device", "caregiver_devices.id", "SET NULL"), nullable=True),
        sa.Column("status", s(24), nullable=False, server_default="pending_verification"),
        sa.Column("requested_at", dt, nullable=False, server_default=NOW),
        sa.Column("resolved_at", dt, nullable=True),
    ], [("ix_drr_account_id", ["account_id"], False)])

    # Session and authentication audit. Raw tokens/secrets are never stored.
    _create(bind, "user_sessions", [
        sa.Column("anonymous_session_id", s(36), _fk("fk_us_anonymous_session", "anonymous_sessions.id", "SET NULL"), nullable=True),
        sa.Column("account_id", s(36), _fk("fk_us_account", "caregiver_accounts.id", "SET NULL"), nullable=True),
        sa.Column("session_hash", s(64), nullable=False, unique=True),
        sa.Column("expires_at", dt, nullable=False),
        sa.Column("revoked_at", dt, nullable=True),
        sa.Column("created_at", dt, nullable=False, server_default=NOW),
    ], [("ix_us_account_id", ["account_id"], False), ("ix_us_expires_at", ["expires_at"], False)])
    _create(bind, "session_refresh_tokens", [
        sa.Column("account_id", s(36), _fk("fk_srt_account", "caregiver_accounts.id"), nullable=False),
        sa.Column("token_hash", s(64), nullable=False, unique=True),
        sa.Column("family_id", s(36), nullable=False),
        sa.Column("expires_at", dt, nullable=False),
        sa.Column("rotated_at", dt, nullable=True),
        sa.Column("revoked_at", dt, nullable=True),
        sa.Column("created_at", dt, nullable=False, server_default=NOW),
    ], [("ix_srt_account_id", ["account_id"], False)])
    _create(bind, "authentication_events", [
        sa.Column("account_id", s(36), _fk("fk_ae_account", "caregiver_accounts.id", "SET NULL"), nullable=True),
        sa.Column("caregiver_id", s(36), _fk("fk_ae_caregiver", "caregivers.id", "SET NULL"), nullable=True),
        sa.Column("event_type", s(40), nullable=False),
        sa.Column("ip_hash", s(64), nullable=True),
        sa.Column("user_agent_hash", s(64), nullable=True),
        sa.Column("occurred_at", dt, nullable=False, server_default=NOW),
    ], [("ix_ae_account_id", ["account_id"], False), ("ix_ae_occurred_at", ["occurred_at"], False)])
    _create(bind, "login_attempt_limits", [
        sa.Column("key_hash", s(64), nullable=False, unique=True),
        sa.Column("window_started_at", dt, nullable=False, server_default=NOW),
        sa.Column("attempts", integer, nullable=False, server_default="0"),
        sa.Column("blocked_until", dt, nullable=True),
        sa.Column("updated_at", dt, nullable=False, server_default=NOW),
    ])
    _create(bind, "multi_factor_authenticators", [
        sa.Column("account_id", s(36), _fk("fk_mfa_account", "caregiver_accounts.id"), nullable=False),
        sa.Column("method", s(40), nullable=False),
        sa.Column("secret_hash", s(128), nullable=True),
        sa.Column("status", s(24), nullable=False, server_default="pending"),
        sa.Column("enrolled_at", dt, nullable=True),
        sa.Column("created_at", dt, nullable=False, server_default=NOW),
    ], [("ix_mfa_account_id", ["account_id"], False)])
    _create(bind, "multi_factor_challenges", [
        sa.Column("account_id", s(36), _fk("fk_mfc_account", "caregiver_accounts.id"), nullable=False),
        sa.Column("challenge_hash", s(64), nullable=False),
        sa.Column("expires_at", dt, nullable=False),
        sa.Column("consumed_at", dt, nullable=True),
        sa.Column("attempts", integer, nullable=False, server_default="0"),
        sa.Column("created_at", dt, nullable=False, server_default=NOW),
    ], [("ix_mfc_account_id", ["account_id"], False)])
    _create(bind, "account_security_events", [
        sa.Column("account_id", s(36), _fk("fk_ase_account", "caregiver_accounts.id", "SET NULL"), nullable=True),
        sa.Column("caregiver_id", s(36), _fk("fk_ase_caregiver", "caregivers.id", "SET NULL"), nullable=True),
        sa.Column("event_type", s(60), nullable=False),
        sa.Column("details", json, nullable=True),
        sa.Column("created_at", dt, nullable=False, server_default=NOW),
    ], [("ix_ase_account_id", ["account_id"], False), ("ix_ase_created_at", ["created_at"], False)])

    # Recovery is a request/verification workflow, never a device-only bypass.
    _create(bind, "account_recovery_requests", [
        sa.Column("account_id", s(36), _fk("fk_arr_account", "caregiver_accounts.id", "SET NULL"), nullable=True),
        sa.Column("email_hash", s(64), nullable=False),
        sa.Column("status", s(24), nullable=False, server_default="pending_verification"),
        sa.Column("requested_at", dt, nullable=False, server_default=NOW),
        sa.Column("resolved_at", dt, nullable=True),
        sa.Column("request_context_hash", s(64), nullable=True),
    ], [("ix_arr_account_id", ["account_id"], False), ("ix_arr_requested_at", ["requested_at"], False)])
    _create(bind, "account_recovery_methods", [
        sa.Column("account_id", s(36), _fk("fk_arm_account", "caregiver_accounts.id"), nullable=False),
        sa.Column("method", s(40), nullable=False),
        sa.Column("value_hash", s(128), nullable=False),
        sa.Column("status", s(24), nullable=False, server_default="pending"),
        sa.Column("verified_at", dt, nullable=True),
        sa.Column("created_at", dt, nullable=False, server_default=NOW),
    ], [("ix_arm_account_id", ["account_id"], False)])
    _create(bind, "account_recovery_challenges", [
        sa.Column("recovery_request_id", s(36), _fk("fk_arc_request", "account_recovery_requests.id"), nullable=False),
        sa.Column("challenge_hash", s(64), nullable=False),
        sa.Column("expires_at", dt, nullable=False),
        sa.Column("consumed_at", dt, nullable=True),
        sa.Column("attempts", integer, nullable=False, server_default="0"),
        sa.Column("created_at", dt, nullable=False, server_default=NOW),
    ], [("ix_arc_request_id", ["recovery_request_id"], False)])
    _create(bind, "account_recovery_events", [
        sa.Column("recovery_request_id", s(36), _fk("fk_are_request", "account_recovery_requests.id", "SET NULL"), nullable=True),
        sa.Column("event_type", s(40), nullable=False),
        sa.Column("actor_account_id", s(36), _fk("fk_are_actor", "caregiver_accounts.id", "SET NULL"), nullable=True),
        sa.Column("details", json, nullable=True),
        sa.Column("created_at", dt, nullable=False, server_default=NOW),
    ], [("ix_are_request_id", ["recovery_request_id"], False)])
    _create(bind, "recovery_code_sets", [
        sa.Column("account_id", s(36), _fk("fk_rcs_account", "caregiver_accounts.id"), nullable=False),
        sa.Column("code_hash", s(64), nullable=False, unique=True),
        sa.Column("used_at", dt, nullable=True),
        sa.Column("created_at", dt, nullable=False, server_default=NOW),
    ], [("ix_rcs_account_id", ["account_id"], False)])
    _create(bind, "account_recovery_cooldowns", [
        sa.Column("account_id", s(36), _fk("fk_arc_account", "caregiver_accounts.id"), nullable=False),
        sa.Column("cooldown_until", dt, nullable=False),
        sa.Column("reason", s(120), nullable=True),
        sa.Column("created_at", dt, nullable=False, server_default=NOW),
    ], [("ix_arc_account_id", ["account_id"], False)])
    _create(bind, "account_credential_changes", [
        sa.Column("account_id", s(36), _fk("fk_acc_account", "caregiver_accounts.id", "SET NULL"), nullable=True),
        sa.Column("credential_type", s(40), nullable=False),
        sa.Column("actor_account_id", s(36), _fk("fk_acc_actor", "caregiver_accounts.id", "SET NULL"), nullable=True),
        sa.Column("changed_at", dt, nullable=False, server_default=NOW),
        sa.Column("details", json, nullable=True),
    ], [("ix_acc_account_id", ["account_id"], False)])

    # Consent history, resource grants, privacy requests, notifications and audit.
    _create(bind, "consent_history", [
        sa.Column("caregiver_id", s(36), _fk("fk_ch_caregiver", "caregivers.id"), nullable=False),
        sa.Column("person_id", s(36), _fk("fk_ch_person", "persons.id"), nullable=False),
        sa.Column("consent_type", s(80), nullable=False),
        sa.Column("version", s(64), nullable=False),
        sa.Column("granted_at", dt, nullable=True),
        sa.Column("withdrawn_at", dt, nullable=True),
        sa.Column("created_at", dt, nullable=False, server_default=NOW),
    ], [("ix_ch_caregiver_id", ["caregiver_id"], False), ("ix_ch_person_id", ["person_id"], False)])
    _create(bind, "identity_access_grants", [
        sa.Column("caregiver_id", s(36), _fk("fk_iag_caregiver", "caregivers.id"), nullable=False),
        sa.Column("person_id", s(36), _fk("fk_iag_person", "persons.id"), nullable=False),
        sa.Column("resource_type", s(80), nullable=False),
        sa.Column("resource_id", s(36), nullable=True),
        sa.Column("access_level", s(24), nullable=False, server_default="read"),
        sa.Column("granted_by_caregiver_id", s(36), _fk("fk_iag_grantor", "caregivers.id"), nullable=False),
        sa.Column("expires_at", dt, nullable=True),
        sa.Column("revoked_at", dt, nullable=True),
        sa.Column("created_at", dt, nullable=False, server_default=NOW),
    ], [("ix_iag_person_caregiver", ["person_id", "caregiver_id"], False)])
    _create(bind, "identity_access_audit", [
        sa.Column("actor_caregiver_id", s(36), _fk("fk_iaa_actor", "caregivers.id", "SET NULL"), nullable=True),
        sa.Column("person_id", s(36), _fk("fk_iaa_person", "persons.id", "SET NULL"), nullable=True),
        sa.Column("action", s(60), nullable=False),
        sa.Column("resource_type", s(80), nullable=True),
        sa.Column("resource_id", s(36), nullable=True),
        sa.Column("details", json, nullable=True),
        sa.Column("created_at", dt, nullable=False, server_default=NOW),
    ], [("ix_iaa_person_id", ["person_id"], False), ("ix_iaa_created_at", ["created_at"], False)])
    _create(bind, "account_deletion_requests", [
        sa.Column("account_id", s(36), _fk("fk_adr_account", "caregiver_accounts.id", "SET NULL"), nullable=True),
        sa.Column("status", s(24), nullable=False, server_default="requested"),
        sa.Column("requested_at", dt, nullable=False, server_default=NOW),
        sa.Column("completed_at", dt, nullable=True),
        sa.Column("details", json, nullable=True),
    ], [("ix_adr_account_id", ["account_id"], False)])
    _create(bind, "account_data_export_requests", [
        sa.Column("account_id", s(36), _fk("fk_ader_account", "caregiver_accounts.id", "SET NULL"), nullable=True),
        sa.Column("status", s(24), nullable=False, server_default="requested"),
        sa.Column("requested_at", dt, nullable=False, server_default=NOW),
        sa.Column("completed_at", dt, nullable=True),
        sa.Column("details", json, nullable=True),
    ], [("ix_ader_account_id", ["account_id"], False)])
    _create(bind, "account_notification_preferences", [
        sa.Column("account_id", s(36), _fk("fk_anp_account", "caregiver_accounts.id", "CASCADE"), nullable=False),
        sa.Column("security_alerts_enabled", boolean, nullable=False, server_default=sa.true()),
        sa.Column("updated_at", dt, nullable=False, server_default=NOW),
        sa.UniqueConstraint("account_id", name="uq_anp_account"),
    ])
    _create(bind, "account_security_notifications", [
        sa.Column("account_id", s(36), _fk("fk_asn_account", "caregiver_accounts.id", "SET NULL"), nullable=True),
        sa.Column("event_type", s(60), nullable=False),
        sa.Column("status", s(24), nullable=False, server_default="queued"),
        sa.Column("created_at", dt, nullable=False, server_default=NOW),
    ], [("ix_asn_account_id", ["account_id"], False), ("ix_asn_status", ["status"], False)])
    _create(bind, "notification_delivery_attempts", [
        sa.Column("notification_id", s(36), _fk("fk_nda_notification", "account_security_notifications.id"), nullable=False),
        sa.Column("channel", s(24), nullable=False),
        sa.Column("status", s(24), nullable=False, server_default="pending"),
        sa.Column("attempted_at", dt, nullable=True),
        sa.Column("failure_reason", txt, nullable=True),
        sa.Column("created_at", dt, nullable=False, server_default=NOW),
    ], [("ix_nda_notification_id", ["notification_id"], False)])
    _create(bind, "idempotency_keys", [
        sa.Column("key_hash", s(64), nullable=False, unique=True),
        sa.Column("scope", s(120), nullable=False),
        sa.Column("response_hash", s(64), nullable=True),
        sa.Column("expires_at", dt, nullable=False),
        sa.Column("created_at", dt, nullable=False, server_default=NOW),
    ])
    _create(bind, "identity_verification_attempts", [
        sa.Column("caregiver_id", s(36), _fk("fk_iva_caregiver", "caregivers.id", "SET NULL"), nullable=True),
        sa.Column("method", s(40), nullable=False),
        sa.Column("status", s(24), nullable=False),
        sa.Column("attempted_at", dt, nullable=False, server_default=NOW),
        sa.Column("details", json, nullable=True),
    ], [("ix_iva_caregiver_id", ["caregiver_id"], False), ("ix_iva_attempted_at", ["attempted_at"], False)])
    _create(bind, "identity_audit_events", [
        sa.Column("actor_caregiver_id", s(36), _fk("fk_iae_actor", "caregivers.id", "SET NULL"), nullable=True),
        sa.Column("person_id", s(36), _fk("fk_iae_person", "persons.id", "SET NULL"), nullable=True),
        sa.Column("action", s(60), nullable=False),
        sa.Column("details", json, nullable=True),
        sa.Column("created_at", dt, nullable=False, server_default=NOW),
    ], [("ix_iae_person_id", ["person_id"], False), ("ix_iae_created_at", ["created_at"], False)])

    # Backfill relationship mappings from authoritative rows already present.
    # This is additive and idempotent; it does not move or delete care data.
    inspector = inspect(bind)
    if inspector.has_table("caregivers") and inspector.has_table("caregiver_person_relationships"):
        caregiver_columns = {column["name"] for column in inspector.get_columns("caregivers")}
        if {"id", "person_id", "relationship"}.issubset(caregiver_columns):
            rows = bind.execute(sa.text("SELECT id, person_id, relationship FROM caregivers WHERE person_id IS NOT NULL")).mappings().all()
            for row in rows:
                exists = bind.execute(sa.text(
                    "SELECT 1 FROM caregiver_person_relationships WHERE caregiver_id=:caregiver_id AND person_id=:person_id"
                ), {"caregiver_id": row["id"], "person_id": row["person_id"]}).first()
                if not exists:
                    bind.execute(sa.text(
                        "INSERT INTO caregiver_person_relationships (id, caregiver_id, person_id, relationship, status, created_at) "
                        "VALUES (:id, :caregiver_id, :person_id, :relationship, 'active', CURRENT_TIMESTAMP)"
                    ), {"id": str(uuid.uuid4()), "caregiver_id": row["id"], "person_id": row["person_id"], "relationship": row["relationship"] or "Family"})

    if inspector.has_table("caregiver_accounts") and inspector.has_table("caregiver_account_links"):
        account_columns = {column["name"] for column in inspector.get_columns("caregiver_accounts")}
        if {"id", "person_id", "caregiver_id"}.issubset(account_columns):
            rows = bind.execute(sa.text("SELECT id, person_id, caregiver_id FROM caregiver_accounts")).mappings().all()
            for row in rows:
                exists = bind.execute(sa.text(
                    "SELECT 1 FROM caregiver_account_links WHERE account_id=:account_id AND caregiver_id=:caregiver_id AND person_id=:person_id"
                ), {"account_id": row["id"], "caregiver_id": row["caregiver_id"], "person_id": row["person_id"]}).first()
                if not exists:
                    bind.execute(sa.text(
                        "INSERT INTO caregiver_account_links (id, account_id, caregiver_id, person_id, status, created_at) "
                        "VALUES (:id, :account_id, :caregiver_id, :person_id, 'active', CURRENT_TIMESTAMP)"
                    ), {"id": str(uuid.uuid4()), "account_id": row["id"], "caregiver_id": row["caregiver_id"], "person_id": row["person_id"]})


def downgrade():
    # Never drop identity/security data automatically. Roll back the revision
    # by restoring a backup and using a separately reviewed data-retention plan.
    raise RuntimeError("Identity/security tables contain audit data; downgrade is intentionally blocked.")
