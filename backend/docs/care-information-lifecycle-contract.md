# SolenOS Care Information Lifecycle Contract

Status: implementation contract for the existing `main` branches  
Scope: backend `getmycareos-star/SolenOS_backend` and caregiver frontend `getmycareos-star/SolenOS-landing`  
Branch policy: changes are made directly to `main`; do not create feature branches or pull requests.

## 1. Product invariant

SolenOS has one authoritative current-care representation per care recipient: the Living Care Record (LCR), labelled **Care Record** in caregiver-facing UI.

The LCR is authoritative for the system's *current supported understanding* of care. It is not the original evidence store and it is not a second chronological activity feed. Original source material and the sequence of changes remain independently retrievable.

The product has three logical layers:

1. **Information history** — immutable or versioned original reports, documents, events, source observations, corrections, actors, and timestamps.
2. **Current understanding** — source-grounded, person-scoped care assertions and derived current-care projections, retaining uncertainty and disagreement.
3. **Caregiver interaction** — focused screens and workflows that capture, review, correct, resolve, preserve, or retrieve information. Screens are projections/workflows, not independent sources of truth.

No read/projection endpoint may mutate authoritative care state as a side effect.

## 2. Screen ownership contract

| Caregiver screen | Owns this interaction | Reads from | Must not become |
|---|---|---|---|
| Care Record | Understand current supported care state: current, uncertain, missing, conflicting, and recently changed information | Canonical current-care projection, assertions, accepted reviews, changes, open matters, source references | A duplicate event feed or manually maintained second record |
| Daily Care | Capture new observations, events, caregiver reports, and corrections to a previously saved report | Existing source history and relevant current-care context for safe review | A second copy of the care record or an independent follow-up store |
| Care Timeline | Browse what was recorded, when, by whom, and how it subsequently changed | Unified source history, correction events, revision history, document and decision events | A place that silently rewrites current facts or owns follow-up status |
| Follow-ups | Discover and manage unresolved questions, actions, decisions, awaited responses, and outcomes | Persisted open loops plus the current attention/uncertainty assessment | An independent source of clinical/care facts |
| Documents | Preserve original files, show extraction/review status, and connect reviewed content to its source | Original evidence/uploads, extraction jobs, review events, linked claims | A store where extracted text silently becomes accepted care state |
| AI Companion | Explain and retrieve saved information, with source/time/uncertainty context | Authorized LCR context and historical source retrieval | An independent memory, authority, or writer of care facts through chat alone |

A screen may link to the owning workflow for an edit. It must not duplicate the write path.

## 3. Canonical backend mapping already present on main

Do not create parallel tables or engines before proving an existing model cannot support the requirement.

| Concern | Existing implementation on main | Contract |
|---|---|---|
| Original source history | `Evidence`, `CareEvent`, document upload/lifecycle records, conversation records | Preserve source payload and source identity; corrections do not overwrite it |
| Typed extracted observations | `EvidenceObservation` | Exact source span, epistemic status, actor, and extraction metadata; correction is a new observation with `supersedes_observation_id` and rationale |
| Care assertions | `CareAssertion` | Source-attributed report/claim, distinct from confirmed fact |
| Review and promotion | `EvidenceReviewRecord`, `EvidenceClaimAssessment`, AI output review/proposal records | Review decisions are versioned; generated candidates do not silently become authoritative |
| Revision history | `CareStateRevision` and ORM revision listener in `models/care_continuity.py` | Before/after snapshots for tracked care-state writes; explicitly disclose partial coverage where a source type is not revision-tracked |
| Longitudinal change | `CareDeltaRun`, `Change`, `Baseline`, `Situation`, `change_intelligence_reconciliation` | Recompute from persisted, person-scoped sources; preserve provenance and uncertainty |
| Unresolved matters | `OpenLoopRecord`, `OpenLoopEvent`, `OpenLoopOccurrence`, `OpenLoopDependency`, plus uncertainty/gap/question models | Open-loop status is workflow state, not a substitute for the underlying care information |
| Current-care retrieval | `build_lcr_context`, `build_unified_lcr_context`, `/care-continuity/current-care` and related care-continuity projections | Return the best supported current understanding with source/coverage/uncertainty limits |
| Shared reconciliation | `execute_care_continuity_reconciliation`, `reconcile_person_change_intelligence` | Caller owns transaction; all source writes must use a defined reconciliation boundary |
| Daily Care Delta | `/care-delta/reconcile`, `reconcile_event`, persisted `CareDeltaRun` | The backend, not browser code, owns longitudinal comparison |
| Source correction receipt | `Correction`, `POST /corrections`, `GET /corrections/{person_id}` | Correction receipt must remain linked to target, person, actor, and reason |
| Evidence observation correction | `POST /care-continuity/evidence/{evidence_id}/observations` | New observation supersedes the earlier observation; original evidence remains intact |

Important: `Correction` and `EvidenceObservation` are not interchangeable. The former records a correction against supported target types; the latter versions a structured, source-span-bound observation. Keep both semantics explicit.

## 4. Source and workflow API responsibilities

The current route families should be reused and normalized rather than replaced:

- Daily capture/source write: existing Daily Care capture and event endpoints in `app/api/v1.py`.
- Delta processing: `POST /care-delta/reconcile`; persisted run inspection: `GET /care-delta/runs/{person_id}`.
- Current understanding: `GET /care-continuity/current-care` and existing current-care/LCR context endpoints.
- Evidence observation and supersession: `POST /care-continuity/evidence/{evidence_id}/observations`.
- Source correction: `POST /corrections`; correction history: `GET /corrections/{person_id}`.
- Unresolved matters: existing `/open-loops` collection, detail, outcome, dependency, and history routes.
- Change and situation review: existing `/change` and `/care-continuity` routes.
- Daily Care history/timeline source bundle: `/daily-care/intelligence/history` and the existing unified care-history projection.
- Documents: existing document lifecycle, extraction, review, and evidence endpoints.
- Companion: existing conversation/message and authorized LCR retrieval endpoints. Chat messages remain conversation history unless a separate explicit review/promotion workflow accepts a care assertion.

All routes must derive person/caregiver scope from the server-validated bearer session or equivalent authorization context. A caller-supplied person ID is a selector, never authority.

## 5. Correction contract

A correction is a new, attributable event. It does not delete or rewrite the original report.

Required correction semantics:

1. Validate that the target exists in the authenticated person's record and that the actor is authorized.
2. Preserve the original target and source evidence.
3. Persist the correction with target type/ID, original text or value, corrected text/value, rationale, actor, and recorded timestamp.
4. Reassess every dependent assertion, change, situation, open loop, current-care projection, and retrieval context.
5. Preserve conflicts where the correction does not conclusively resolve them.
6. Return an explicit processing receipt. Never label propagation complete until all required stages have completed.
7. Make retry idempotent. A retry after an ambiguous network failure must not create duplicate corrections or duplicate derived state.
8. Keep a queryable link from the corrected target to the correction and from the correction to affected derived records.

The current `POST /corrections` path commits the correction receipt before running reconciliation and returns a pending receipt if reconciliation fails. This avoids losing the correction, but the durable retry/recovery mechanism and cross-consumer freshness guarantee must be proven. Do not represent a pending correction as fully propagated.

For typed evidence observations, use append-only supersession. Enforce one active successor per observation, same-person scope, same subject/attribute, and a non-empty correction rationale. A correction of the latest version must point to that latest version, not an older ancestor.

## 6. Cross-screen propagation contract

For a correction or accepted review, expected outcomes are:

| Surface | Required result |
|---|---|
| Daily Care | Show the original entry as corrected and expose the corrected version; do not erase the earlier report |
| Care Record | Recompute affected current understanding; show source and uncertainty; do not retain an unsupported stale claim as current |
| Timeline | Show original event and correction in chronological/recorded order, distinguishing event time from recorded time |
| Follow-ups | Reassess linked matters; close only when the defined verification/resolution condition is satisfied |
| Documents | Keep original file bytes unchanged; re-evaluate extracted claims/reviews only through their own review workflow |
| AI Companion | Retrieve current supported state and, when asked, the correction history; do not answer from superseded content as if current |

The browser may refresh read projections after the backend reports durable processing. It must not run a second reconciliation or infer success from a successful source-save response alone.

## 7. Transaction, retry, and freshness requirements

- Every source write has an explicit transaction boundary and a durable execution receipt.
- The receipt distinguishes source persistence from downstream reconciliation (for example: `source=committed`, `reconciliation=pending`, `projection=stale`).
- Reconciliation is idempotent by source/correction ID and workflow version.
- A durable retry mechanism recovers pending work after process restart; in-memory retry alone is insufficient.
- Failed reconciliation cannot be converted into an empty/healthy projection.
- Projection responses expose coverage/freshness limitations where required.
- AI retrieval must either use a current projection or explicitly disclose that current-care reconciliation is pending/stale; it must not silently answer from stale context.
- All reads and writes remain person-scoped and caregiver-authorized.

Do not add a generic event bus, new microservice, or new state store unless current persistence/worker mechanisms demonstrably cannot satisfy these requirements.

## 8. Frontend implementation boundaries

- The Care Record page reads current-care projections and shows current understanding, changes, uncertainty, and concise next actions. Historical detail links to Timeline.
- Daily Care owns capture and correction entry points. It may show a small recent-save confirmation, but the full browseable history belongs in Timeline.
- Timeline is a read-oriented, unified history. A correction action may launch the shared correction workflow; the Timeline itself does not maintain a second current-care state.
- Follow-ups owns open-loop lifecycle and outcome reporting. Reporting an update is not the same as resolving an issue.
- Documents owns original-file review and explicit acceptance/rejection of extracted candidates.
- Companion is conversational retrieval. It may explain sources and offer a link to the owning screen; it cannot write LCR facts merely because the user said something in chat.
- After successful backend processing, dispatch one shared refresh signal (or use a server revision cursor) so affected projections refresh. Do not treat a browser event as the source of truth.
- Caregiver-facing language stays plain and avoids internal terms such as Delta Engine, reconciliation, provenance, assertion, and epistemic state.

## 9. Blocking verification matrix

The following are acceptance tests, not claims of current completion:

1. Daily Care source write persists and is retrieved in a new session with the same source ID and actor.
2. A source correction is saved once and its correction receipt is queryable.
3. A correction to a CareEvent and a CareAssertion triggers the same canonical reconciliation boundary.
4. A typed observation correction preserves the original observation and source evidence and points to the latest predecessor.
5. A correction invalidates/reassesses all dependent assertions and derived changes; no stale unsupported current claim remains.
6. Conflicting reports remain visible and are not resolved solely by recency.
7. An open loop linked to a corrected source is reassessed; it is not auto-closed without its verification rule.
8. The Care Record shows the corrected supported understanding and its source.
9. Timeline shows both the earlier report and correction, with occurred/recorded times distinguished.
10. Documents' original bytes and extracted source text remain unchanged after a claim correction.
11. Companion retrieves the corrected current understanding and can identify the superseded report when historical context is requested.
12. A simulated reconciliation failure leaves a durable pending receipt and is recovered after worker/process restart without duplicate side effects.
13. A stale or partial projection is explicitly labelled; it is never presented as a complete empty record.
14. Cross-person and cross-caregiver authorization tests reject reads and writes outside the authorized care circle.
15. A complete PostgreSQL HTTP journey verifies the real frontend-to-backend contract; mocks and SQLite-only tests do not satisfy this gate.

## 10. Main-only execution sequence

All work is committed directly to `main`, in small, reviewable commits. Before each write, re-read the current `main` head and target file SHA. If the head moved, rebase the intended change conceptually and revalidate; never force-push.

1. **Inventory and baseline:** map current main models, routes, migration graph, workflows, frontend consumers, and test results. Record exact gaps; do not assume open/closed PR state proves code is present.
2. **Close the correction reliability gap:** make correction receipts idempotent and durably retryable; ensure the correction and its execution state cannot be confused.
3. **Complete source-to-LCR reconciliation:** route Daily Care, document review, accepted Companion proposals, and correction writes through the existing shared coordinator, with source IDs and protected assertion IDs.
4. **Make projections revision-aware:** current Care Record, Timeline, Follow-ups, and Companion must expose/consume freshness and source lineage consistently.
5. **Enforce screen boundaries:** remove duplicate history/follow-up ownership from Daily Care and current-state editing from Timeline; keep navigation to the owning workflow.
6. **Verify:** run focused backend tests, PostgreSQL integration/migration validation, frontend typecheck and UI contract checks, then end-to-end browser/API workflows. Report each result with the exact commit and workflow run.

## 11. Known branch/PR handling

This contract is written against the current `main` branches only. A pull request is not part of production/main until its changes are present on `main` and verified there. In particular, inspect migrations and the Alembic graph before selectively porting any closed or abandoned work: revision IDs and schema repairs may already exist on main under different filenames. Never merge or cherry-pick an entire stale branch simply because its PR description sounds relevant.

## 12. Verified main-branch baseline (2026-11-15)

The following are code-level findings from the current `main` snapshots, not assumptions based on PR descriptions:

### Already present

- The backend already has `Evidence`, `CareEvent`, `CareAssertion`, `EvidenceObservation`, `EvidenceReviewRecord`, `EvidenceClaimAssessment`, and `CareStateRevision`.
- Structured evidence-observation corrections already use append-only supersession through `supersedes_observation_id` and `correction_rationale`, and validate the source passage against the original evidence.
- `POST /corrections` already validates target ownership, persists a correction receipt, and invokes the shared `execute_care_continuity_reconciliation` coordinator.
- The shared coordinator calls `reconcile_person_change_intelligence` and deliberately leaves transaction ownership with the API caller.
- Daily Care already has durable extraction jobs and execution-status metadata; extraction completion and current-care projection verification are represented separately in the capture workflow.
- Follow-ups already use the persisted Open Loops model and expose event history; the UI distinguishes a caregiver update from a verified resolution.
- The Care Record already reads current-care projections and persisted change history. The Timeline already has a unified history view and correction affordances.
- Existing PostgreSQL integration coverage includes Daily Care persistence, LCR retrieval, Companion context, document isolation, and cross-person access boundaries.

### Verified gaps requiring implementation and proof

1. **Correction execution status is not durably stored on the correction row.** The current `Correction` model stores target, original/corrected text, reason, caregiver and creation time. The `POST /corrections` handler builds a `care_continuity_execution` object on the returned ORM instance after the correction has been committed. On reconciliation failure it returns `status=pending` and `retry_required=true`, but that execution receipt is not a persisted field on `Correction`. A process restart therefore cannot recover the pending state from that row alone.
2. **Correction retry/idempotency is incomplete.** The current correction request schema does not establish a stable caller idempotency key, and the route does not expose a durable correction-processing retry contract. A retry after an ambiguous response must not create a second correction or repeat side effects.
3. **Cross-consumer freshness is not yet proven.** The main audit records that the persisted Delta history is visible in the primary LCR, but the full create/correct/reconcile/restart/retrieve journey across Care Record, Timeline, Follow-ups, Documents, and Companion still needs a PostgreSQL-backed test and deployed verification.
4. **Screen ownership is not yet fully enforced in UI.** Existing Daily Care, Care Record, Timeline and Follow-ups components have some overlapping summaries/history/attention displays. This is acceptable only for small contextual previews; each full workflow and full history must have one owning screen, with links to that owner.
5. **The existing mainline must be treated as canonical.** Backend PR #506 is closed without merge. Its branch contains migrations with revision IDs `20261115_01_situation_states` and `20261115_02_repair_caregiver_transfers`; current backend `main` already contains those revision IDs under filenames `20261115_01_situation_trajectory_states.py` and `20261115_02_repair_responsibility_transfer_table.py`. Do not cherry-pick or recreate those migrations. Backend PR #504 is still open and is not part of `main`; its multilingual Companion work must not be treated as implemented unless separately verified on main.

### Immediate implementation priority

The next code change should address correction durability before broad UI rearrangement:

- Add persisted processing state to the existing correction record (or prove an existing durable execution ledger can own it).
- Add a stable idempotency contract for correction submission.
- Make reconciliation retryable from persisted correction/source IDs without creating duplicate correction history or duplicate derived state.
- Expose a person-scoped correction status/read endpoint or include durable status in the correction-history response.
- Add PostgreSQL tests for success, injected reconciliation failure, process/session restart, retry, duplicate submission, and cross-person denial.
- Only then update all frontend projections to refresh from the durable server revision/status.

Do not claim this gap is fixed by the documentation commit.

