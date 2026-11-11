"""Single source of truth for enabled Qwen workflow identifiers.

The API schema and background worker both import this contract. Community is
intentionally excluded until the Community product workflow is explicitly
enabled and governed.
"""
from typing import Literal

ENABLED_WORKFLOWS = (
    "daily_capture", "add_record", "document", "living_care_record",
    "care_history_search", "care_situation_change", "delta_engine",
    "evidence_provenance", "uncertainty_contradictions", "returning_caregiver",
    "appointment", "after_appointment", "handoff", "professional_summary",
    "open_loops", "two_week_review", "decision_outcome", "medication_day",
    "reminders_notifications", "care_coordination", "companion",
    "caregiver_feedback", "sharing_export", "roles_access", "account_recovery",
    "settings_security", "record_lifecycle", "offline_sync", "ai_reliability",
    "audit_review", "care_transition", "incident_follow_through",
    "emergency_readiness", "care_coverage_gap", "care_instruction_change_tracker",
    "critical_information_completeness", "personal_preferences_distress_context",
    "caregiver_strain_support", "document_validity_renewal",
)

QwenWorkflow = Literal[*ENABLED_WORKFLOWS]

if len(ENABLED_WORKFLOWS) != len(set(ENABLED_WORKFLOWS)):
    raise RuntimeError("Qwen workflow identifiers must be unique")
if "community" in ENABLED_WORKFLOWS:
    raise RuntimeError("Community must remain disabled until explicitly governed")
