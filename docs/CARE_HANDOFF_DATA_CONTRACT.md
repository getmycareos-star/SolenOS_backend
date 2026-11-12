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

## Current endpoint boundary

\`POST /api/v1/handoff-drafts/prepare\` remains a read-only preparation endpoint. It does not create a recipient-bound handoff, deliver information, authenticate a professional, establish professional access, or mutate the LCR.

The preparation endpoint reads the existing allowlisted LCR facts, decisions/outcomes, open questions, information gaps, timeline entries, care events/transitions, appointments, Daily Care observations, and uploaded-document/extraction records when available. Action records remain limited to the requesting caregiver's eligible linked evidence. Community is not an authorized source.

For professional-audience drafts, medication output is limited to unambiguous caregiver-reviewed current medication assertions within their configured review window. It is a caregiver report, not independent clinical verification and not proof of administration. No medication list is inferred from incidental document or narrative text. No verified person-scoped allergy adapter exists; allergy coverage is explicitly unavailable and must never be interpreted as "no allergies".

\`POST /api/v1/handoff-drafts/{draft_id}\` saves a caregiver revision. Source sections must remain byte-for-byte equivalent to the retrieved source sections. Generated statements must retain exact source-reference validation. Caregiver-authored notes are separately attributed as unverified reports and cannot be promoted to source facts.

\`POST /api/v1/handoff-drafts/{draft_id}/approve-export\` approves only the requesting caregiver's professional-audience draft, after source freshness is rechecked and the saved revision hash is verified. \`GET /api/v1/handoff-drafts/{draft_id}/export.pdf\` returns a PDF only for that exact approved revision, repeats the freshness and revision-hash checks, and records an export audit event. The PDF includes caregiver-facing evidence labels, source IDs and available source/reporter references, separate event/record times, source availability, medication-report limitations, and the explicit allergy-data limitation. Responses are marked \`Cache-Control: no-store\`.

External sharing is caregiver-controlled through the device share sheet or manual PDF attachment. SolenOS does not email the PDF, issue an external share link, register a professional recipient, create a recipient portal, or provide the recipient access to the SolenOS record. The local file may be retained or forwarded by the recipient; SolenOS cannot revoke a file after it leaves the device.

The draft stores a versioned source snapshot and revisions. Approval is bound to the exact saved revision and source fingerprints; export fails closed when the source cannot be revalidated or has changed. This is not a transactionally consistent database snapshot across all source tables, and the export must not be represented as clinical validation or professional receipt/acceptance.

## Release limitations

- Allergy/reaction data is unavailable until a dedicated person-scoped, provenance-preserving adapter is implemented and tested. The PDF must state that unavailable does not mean absent.
- Routines/preferences, contacts, measurements, professional identity/credentials, professional receipt/acceptance and recipient access are not part of this MVP.
- No schema migration is required for caregiver approval/PDF export; existing draft/revision/audit persistence is reused.
- Production behavior remains unverified until the backend PR is merged and the existing deployment is updated through the separately approved release process.

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
