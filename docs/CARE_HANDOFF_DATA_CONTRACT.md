# Care Handoff Data Contract and Safety Invariants

Status: engineering contract. This document is not evidence of clinical validation or production readiness.

## Authority and snapshot

- The Living Care Record (LCR) remains the authoritative source of care facts.
- A handoff is a derived, recipient-specific snapshot with a stable snapshot ID, source references, preparation timestamp/timezone, and source-version/hash information where available.
- A draft, generated text, acknowledgement, or AI interpretation must never overwrite or become independent proof of an LCR fact.
- Do not claim a complete record when a source adapter is absent, results are bounded, or a query returns no rows.

## Source provenance

Each material item should carry:
- source type/table and stable source ID;
- event/effective time, record-entered/updated time, and due time as separate fields where available;
- author/reporter/observer and role where recorded;
- epistemic status (observed, reported, professional finding, verified diagnosis, interpretation, uncertain, conflicting, missing), kept separate from workflow status;
- freshness/stale indicator based on timestamps, never inferred from wording;
- correction/version lineage where available.

Missing metadata stays missing. The system must not fabricate a source, author, date, or certainty label.

## Recipient-specific templates

The family audience is plain-language and limited to practical continuity, routines/preferences, verified health/medication information, next actions, safety contacts/instructions, coordination and receipt.

The professional audience includes relevant family content plus transition reason, documented baseline, evidence/provenance register, medication reconciliation/discrepancies, clinical/functional observations, active issues, authorised care plan, pending results, action register, contingency, record integrity and formal receipt.

Template section presence does not mean the section is populated or verified. Each section carries coverage_status and a clear coverage note. Fields with no supported source are omitted or explicitly marked not recorded/not assessed/unknown as appropriate.

## Actions and responsibility

Each action should have an owner, due time or condition, status, source, and outcome. Preparing/sending a handoff does not complete an action. Unaccepted actions remain open and have an escalation owner. A recipient's receipt and acceptance are separate events; sending is not acceptance.

## Access and audit

- Resolve person/caregiver scope from the server-issued session; never trust a browser-supplied person ID as authority.
- Enforce recipient identity, role, purpose, consent/authorization basis, scope, expiry and revocation on the server.
- Use least-privilege disclosure. Original documents require separately authorized byte storage/access.
- Audit prepare, review, create, send, read, receipt, clarification, accept/decline, revoke and action completion as distinct events.
- Audit logs should be content-minimizing; do not copy sensitive care text into metadata.
- Fail closed when required audit persistence or authorization checks fail.

## Current endpoint boundary (verified against backend main after PR #471)

`POST /api/v1/handoff-drafts/prepare` remains a read-only preparation endpoint. It does not create a recipient-bound handoff, send or share information, establish professional access, or mutate the Living Care Record.

The endpoint reads a fixed allowlist of care facts, decisions and outcomes, open questions, information gaps, timeline entries, care events, care transitions, appointments, evidence freshness records, Daily Care observations, and document/extraction records when their adapters are available. Daily Care and document retrieval now have explicit adapters. Document records are person-scoped rather than restricted to the requesting caregiver; the requesting caregiver's private action evidence remains excluded from another caregiver's draft. This is a person-scope rule, not proof of a separately recorded caregiver consent grant. Do not describe it as professional authorization or unrestricted sharing.

Medication/allergy reconciliation, routines/preferences, contacts, measurements, and authenticated professional identity/credentials do not have verified dedicated handoff adapters in this endpoint. The professional template includes a medication-reconciliation checklist, but its coverage must remain `not_assessed` unless a verified source adapter is added. Appointment details or medication-related narrative text are not a verified medication list. Do not infer medication name, dose, route, schedule, adherence, allergy, diagnosis, or clinical instruction from incidental text.

The `audience: professional` value selects a professional-oriented draft template; it does not authenticate a clinician or establish that the recipient is authorized. Community is not an authorized source. Source references retain provenance and separate event, record, and due times. Active LCR facts may be included even when older than the selected event window; superseded historical facts are included only when their record timestamp falls inside that window and are labelled superseded. Source coverage is a retrieval checklist, not clinical completeness or clinical validation.

The draft is not yet a transactionally consistent immutable snapshot: source revision fingerprints are used for stale-draft checks, but full snapshot/version semantics, recipient-bound consent/authorization, expiry/revocation, professional receipt/acceptance, and live production end-to-end verification remain release blockers.

## Required tests before release

1. Missing/invalid session rejected.
2. Cross-person data cannot be read or inferred through empty/partial response.
3. Source adapter absent versus empty versus unavailable are distinguished.
4. Provenance IDs and timestamps survive response and handoff persistence.
5. Corrections retain history and invalidate stale reviewed drafts.
6. Medication discrepancies never auto-resolve and no medication-change advice is produced.
7. Owner/deadline/status/outcome for actions persist and are not auto-completed.
8. Review changes invalidate approval; share requires explicit review and authorized recipient scope.
9. Receipt, questions, acceptance/decline and unaccepted responsibility persist.
10. Expiry/revocation blocks access and original document bytes are access-controlled.
11. Audit failure fails closed.
12. Live Railway E2E proves authenticated save/read/restart/isolation; unit or mocked tests alone are insufficient.
