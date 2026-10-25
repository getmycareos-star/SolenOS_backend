# Qwen coverage across SolenOS workflows

## Contract

Qwen is a read-only support layer. The caregiver and the Living Care Record (LCR) remain authoritative. Every returned suggestion requires caregiver review; Qwen must never create or alter LCR facts, assign a handoff, change a reminder, grant access, or claim a save occurred. If source context is missing or a claim cannot be grounded, the system should ask for clarification or fail closed rather than inventing details.

Community is intentionally excluded from Qwen workflow selection and inference. The disabled workflow is not counted among the 30 enabled workflows.

## Enabled workflow matrix

| Workflow ID | Intended task | Context rule |
|---|---|---|
| `daily_capture` | Organize today's caregiver-reported observations | Use supplied draft/evidence only; do not treat unsaved text as saved |
| `add_record` | Structure a proposed entry or correction | Use supplied text and authorized existing care events for comparison |
| `document` | Explain a document or extracted text | Use only explicitly attached, authorized evidence |
| `living_care_record` | Explain current recorded care context | Read only current session's authorized care events and relevant appointments |
| `care_history_search` | Summarize matching historical information | Read only the current care record; no match does not prove an event never happened |
| `care_situation_change` | Compare reported current and prior observations | Compare supplied/authorized events; never infer cause |
| `delta_engine` | Prepare possible changes and contradictions for review | Treat output as a candidate, not a reconciled fact or write |
| `evidence_provenance` | Identify source and status of claims | Preserve caregiver report, document text, professional-reported information and inference as distinct |
| `uncertainty_contradictions` | Surface conflicts, ambiguity and missing sources | Do not resolve contradictions by guessing |
| `returning_caregiver` | Orient a returning caregiver to recent changes | Use recent authorized events and relevant appointments; do not invent a timeline |
| `appointment` | Prepare appointment questions | Use recorded appointment and authorized care context; distinguish requested from confirmed |
| `after_appointment` | Organize post-appointment notes | Use supplied caregiver notes/evidence; never invent clinician words or outcomes |
| `handoff` | Draft a care handoff | Use authorized events and handoff records; never invent recipient or assignment |
| `professional_summary` | Draft a professional-facing summary | Use authorized events and relevant appointment context; preserve provenance and uncertainty |
| `open_loops` | Identify unresolved questions/follow-ups | Include only explicitly evidenced open items; do not invent owners or deadlines |
| `two_week_review` | Review the previous two weeks | Limit care-event context to the last 14 days; no causal claims |
| `decision_outcome` | Separate a reported decision from its outcome | Keep decision, outcome, source and timing separate |
| `medication_day` | Organize medication observations | Observation-only; no medication or dose changes/advice |
| `reminders_notifications` | Explain reminders/notifications and pending items | Read only notifications for the authenticated person and caregiver; do not change status |
| `care_coordination` | Organize responsibilities and coordination | Use authorized events, relevant appointments and handoff records; do not assign work |
| `companion` | Respond to the caregiver's immediate request | Use authorized context where relevant; never pretend to have personal caregiver experience |
| `caregiver_feedback` | Help phrase feedback/correction | Compare against authorized recorded events where useful; do not overwrite facts |
| `sharing_export` | Prepare a shareable summary draft | Use authorized events and relevant appointments; do not claim anything was shared/exported |
| `roles_access` | Explain roles and professional access | Use authoritative supplied system state only; never grant access |
| `account_recovery` | Explain account-recovery steps | Use supplied state only; never ask for passwords, codes or recovery secrets |
| `settings_security` | Explain security/privacy settings | Do not claim a setting is saved without authoritative confirmation |
| `record_lifecycle` | Explain pin/archive/restore/delete actions | Explain only documented effects; Qwen does not execute lifecycle changes |
| `offline_sync` | Explain offline/sync state | Distinguish local pending edits from server-confirmed persistence |
| `ai_reliability` | Explain Qwen availability and retry state | Report only observed status; never fabricate successful inference |
| `audit_review` | Summarize review candidates/audit details | Preserve proposed/accepted/rejected/persisted status; never claim a write occurred |

## Validation gates

1. Schema contract tests accept exactly these 30 IDs and reject `community` and unknown IDs.
2. Backend CI runs authenticated inference against the actual bundled Qwen model for each enabled ID.
3. The same run checks non-empty generated output, provider availability, review-required state and `written_to_lcr=false`.
4. Daily Capture candidate extraction is checked separately from the read-only assistance path.
5. PostgreSQL migration/persistence and identity-isolation tests must pass on the current migration chain.
6. Production rollout is not complete until the latest safe backend deployment is live and authenticated smoke tests confirm the exact deployed revision.

## Background processing boundary

The current shared assistant endpoint runs synchronously for a caregiver request. This matrix does **not** claim that a durable asynchronous job queue or autonomous background analysis across all workflows exists. Any future background worker must use the same record-scoped authorization, source provenance, idempotency, review-before-write and fail-closed rules; it must not silently write LCR facts.
