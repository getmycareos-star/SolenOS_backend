# Shared Qwen support across SolenOS workflows

## Purpose

Qwen is one optional, shared language-assistance layer used from the Companion and relevant workflow surfaces. It is not a source of truth, authorization engine, care-continuity engine, reminder scheduler, document store, or professional decision-maker.

The backend is authoritative for identity, permissions, retrieved context, persistence, record lifecycle, notification status, and review attribution. The Qwen assist endpoint is read-only and returns `written_to_lcr: false`.

## Workflow contract (31)

| Workflow ID | Scope | Required boundary |
|---|---|---|
| `daily_capture` | Organize caregiver-reported events | Do not infer unreported facts or diagnosis |
| `add_record` | Structure a proposed entry or correction | No save or correction from Qwen |
| `document` | Explain supplied/authorized document text | Separate source text from interpretation |
| `living_care_record` | Explain authorized LCR context | LCR remains authoritative |
| `care_history_search` | Answer from retrieved care history | Admit when evidence is absent |
| `care_situation_change` | Explain supported changes | No unsupported cause or diagnosis |
| `delta_engine` | Explain reconciled differences | Do not replace or invent engine output |
| `evidence_provenance` | Explain source support | Never invent a source or verification |
| `uncertainty_contradictions` | Identify conflicts and gaps | Preserve Known/Uncertain/Missing/Needs clarification |
| `returning_caregiver` | Summarize changes since prior review | Require a real comparison reference |
| `appointment` | Prepare appointment questions | No treatment recommendation |
| `after_appointment` | Structure caregiver-reported outcomes | Do not claim independent verification |
| `handoff` | Draft a care handoff | Does not send, assign, or acknowledge |
| `professional_summary` | Draft a source-aware professional summary | No false professional endorsement |
| `open_loops` | Explain recorded unresolved matters | No invented tasks, owners, or deadlines |
| `two_week_review` | Review the last 14 days | State timeframe and data gaps; no causation |
| `decision_outcome` | Organize decisions and reported outcomes | Do not infer implementation or causality |
| `medication_day` | Organize medication-day observations | No prescribing or dose-change advice |
| `reminders_notifications` | Explain persisted reminder state | Scheduler/delivery status comes from backend |
| `care_coordination` | Draft coordination language | No invented assignment or completion |
| `community` | Summarize dementia peer experiences | Peer stories are not individual care facts |
| `companion` | General conversational support | Authorized context only; no fake fallback |
| `caregiver_feedback` | Revise current interpretation from feedback | No silent history edits or false model-learning claims |
| `sharing_export` | Draft a recipient-specific summary | Does not share/export content |
| `roles_access` | Explain supplied role/access status | Cannot grant or verify access |
| `account_recovery` | Explain session/account recovery | Never ask for credentials or expose secrets |
| `settings_security` | Explain settings and privacy | Does not change controls |
| `record_lifecycle` | Explain pin/archive/restore/delete | Does not execute lifecycle actions |
| `offline_sync` | Explain supplied sync state | Never claim unconfirmed save/sync |
| `ai_reliability` | Explain model/service failure state | No fabricated or canned inference response |
| `audit_review` | Explain review provenance/status | Cannot approve its own candidate |

## Context handling

The endpoint retrieves only the current bearer session's person/caregiver scope. Evidence IDs are checked against that care record and the current caregiver attribution. Workflow-specific queries should receive only the context needed for the task. A request-body ID is never proof of ownership.

## What the contract test proves

`backend/tests/test_qwen_all_workflow_contract.py` verifies that all 31 workflow identifiers are accepted by the Pydantic request schema and an unknown identifier is rejected. It does **not** prove that Qwen inference is available, that every frontend screen is connected, that each workflow retrieves all relevant context, or that the production deployment is healthy.

## Required verification before release

1. Run the backend test suite and backend validation workflow.
2. Confirm the frontend TypeScript/build checks pass on the workflow selector changes.
3. With a valid anonymous bearer session, call `GET /api/v1/assistant/status` and require the backend to identify the configured local Ollama runtime as available.
4. For each of the 31 workflow IDs, call `POST /api/v1/assistant/assist` with a real non-sensitive test request and verify `ai_generated=true`, `provider_status=available`, `written_to_lcr=false`, and `persistence=read_only_assistance`.
5. Confirm invalid/expired bearer sessions are rejected and a second care record's evidence cannot be retrieved by the first session.
6. Confirm model outage yields an explicit error; no fabricated answer appears and no care record is changed.
7. Test candidate extraction separately: source quote is preserved, candidate status is `pending_review`, accept/reject records the actual caregiver, and neither operation silently writes an authoritative LCR fact.
8. Verify production database migration history against the actual database before any deployment or merge that depends on PR #86 migrations. Do not stamp or rewrite production migration history to silence a missing revision.

## Known release blocker

The previously observed Railway deployment could not locate Alembic revision `20261024_11`, and an authenticated Companion request returned HTTP 503. These changes must not be described as live-verified until the migration chain is reconciled safely and the live inference path is retested.
