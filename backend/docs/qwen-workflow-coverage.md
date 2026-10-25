# Shared Qwen support across SolenOS workflows

## Purpose and authority

Qwen is an optional language-assistance layer, not the source of truth, authorization engine, care-continuity engine, reminder scheduler, document store, or professional decision-maker. The backend remains authoritative for identity, permissions, retrieved context, persistence, and review attribution. Assistance must not write to the Living Care Record (LCR); responses must preserve `written_to_lcr: false`.

## Workflow contract (30)

| Workflow ID | Scope | Required boundary |
|---|---|---|
| `daily_capture` | Organize caregiver-reported events | Do not infer diagnosis, cause, or unreported facts |
| `add_record` | Structure a proposed entry or correction | Does not save or correct a record |
| `document` | Explain supplied or authorized document text | Separate source text from interpretation |
| `living_care_record` | Explain authorized LCR context | LCR remains authoritative |
| `care_history_search` | Answer from retrieved care history | Admit when evidence is absent |
| `care_situation_change` | Explain supported changes | No unsupported cause or diagnosis |
| `delta_engine` | Explain reconciled differences | Do not invent engine output |
| `evidence_provenance` | Explain source support | Never invent a source or verification |
| `uncertainty_contradictions` | Identify conflicts and gaps | Preserve Known/Uncertain/Missing/Needs clarification |
| `returning_caregiver` | Summarize changes since a prior review | Require a real comparison reference |
| `appointment` | Prepare appointment questions | No treatment recommendation |
| `after_appointment` | Structure caregiver-reported outcomes | Do not claim independent verification |
| `handoff` | Draft a care handoff | Does not send, assign, or acknowledge |
| `professional_summary` | Draft a source-aware summary | No false professional endorsement |
| `open_loops` | Explain recorded unresolved matters | No invented tasks, owners, or deadlines |
| `two_week_review` | Review a two-week period | State data gaps; no causation |
| `decision_outcome` | Organize decisions and reported outcomes | Do not infer implementation or causality |
| `medication_day` | Organize medication-day observations | No prescribing or dose-change advice |
| `reminders_notifications` | Explain persisted reminder state | Backend owns schedule/delivery status |
| `care_coordination` | Draft coordination language | No invented assignment or completion |
| `companion` | General conversational support | Authorized context only; no canned fallback |
| `caregiver_feedback` | Revise current interpretation from feedback | No silent history edits or false model-learning claims |
| `sharing_export` | Draft a recipient-specific summary | Does not share or export |
| `roles_access` | Explain supplied role/access status | Cannot grant or verify access |
| `account_recovery` | Explain account/session recovery | Never expose credentials or secrets |
| `settings_security` | Explain supplied settings/privacy facts | Does not change controls |
| `record_lifecycle` | Explain pin/archive/restore/delete | Does not execute lifecycle actions |
| `offline_sync` | Explain supplied sync state | Never claim unconfirmed save/sync |
| `ai_reliability` | Explain service/model failure state | Never fabricate inference success |
| `audit_review` | Explain review provenance/status | Cannot approve its own candidate |

## Verification boundaries

The contract test verifies that all 30 enabled workflow identifiers are accepted by the Pydantic request schema and an unknown identifier is rejected. It does not prove model availability, complete workflow-specific retrieval, frontend connectivity, or production health.

The backend integration workflow additionally exercises each identifier against its configured Qwen runtime. Release still requires authenticated production checks: assistant status reports the real configured local runtime as available; every workflow returns model-generated output with `provider_status=available` and `written_to_lcr=false`; and an explicit review decision is required for persisted candidates. If inference is unavailable, return an explicit error rather than canned output.


## Background execution status

These 30 workflow IDs are synchronous, request-triggered assistance contracts, not 30 autonomous background jobs. Current implementation does not establish a durable Qwen job queue or scheduled worker for all workflows. Qwen must not independently create reminders, send handoffs, alter records, approve candidates, or continuously inspect care data. Those actions require explicit backend-owned state transitions and authorization. Any future background inference must use a durable queue, idempotency, bounded retries, status tracking, caregiver-scoped authorization, and explicit review before LCR changes. Do not claim background processing until those components and production behavior are verified.

Community Qwen is disabled: the `community` workflow is not accepted, and the community experience summary endpoints return HTTP 410 without invoking the model.
