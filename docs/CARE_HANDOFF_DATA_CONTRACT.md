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

POST /api/v1/handoff-drafts/prepare is a person- and caregiver-session-scoped, read-only briefing preparation path. It reads a fixed allowlist of care facts, care decisions and outcomes, open questions, information gaps, uncertainty assessments, timeline entries, care events, care transitions, appointments, freshness records and caregiver-attributed actions. It also uses separately scoped adapters for persisted Daily Care entries and uploaded documents/extracted text. Actions are included only when their linked evidence belongs to the requesting caregiver; evidence from another caregiver is excluded. Community is not authorised and is never a care-record source. Medication/allergy reconciliation, routines/preferences, contacts, and structured measurements must remain explicitly unavailable/not assessed until dedicated scoped adapters are implemented and tested. Professional identity is not inferred from a briefing request. The endpoint does not mutate the LCR.

Qwen receives only the person-scoped source records selected from the LCR snapshot for the requested purpose and audience. It generates a concise paraphrase per selected source and must return an exact supporting quotation copied from that source. The backend attaches the original source text, rejects unknown source references and rejects supporting quotations that do not exactly occur in the corresponding source text. Qwen does not create, correct, promote, or become the authority for LCR facts. Previous Companion dialogue and model memory are not generation sources. The saved briefing retains the full retrieved source snapshot, per-source availability and truncation metadata; Qwen's small-model context is deliberately bounded and therefore does not mean every retrieved record was paraphrased.

The quote check proves only that the supporting wording exists in the saved snapshot. It does not prove semantic entailment of the generated paraphrase or clinical truth. Every generated paraphrase therefore remains a draft requiring caregiver review; professional use requires review by an appropriately authorized person. Medication absence must never be inferred from missing medication records. Active LCR facts may be included when older than the requested event window; historical facts, event time and record time remain distinct. Source truncation and unavailable adapters make retrieval visibly incomplete. Audience-specific template coverage is a checklist, not a clinical completeness score.

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
