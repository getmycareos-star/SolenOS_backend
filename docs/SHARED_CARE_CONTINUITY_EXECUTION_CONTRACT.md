# Shared Care-Continuity Execution Contract

**Authority:** Backend `main` is the execution authority. Frontend `main` is a consumer of persisted contract state. This document applies to every enabled workflow. It does not create a second care-record architecture.

**Status:** Contract definition committed to `main`; implementation coverage is not implied by this document. Each workflow remains incomplete until its exact source-to-care-record journey is covered by current-main tests and persisted read-back evidence.

## 1. Purpose

Every enabled workflow must execute the same care-continuity lifecycle. A workflow may have a specialised input adapter or extractor, but it may not invent its own rules for provenance, epistemic status, review, reconciliation, retrieval, correction, or failure handling.

The shared lifecycle is:

1. Input received.
2. Original source preserved.
3. Source evidence persisted and linked to the authenticated person and caregiver.
4. Extraction performed when applicable; extraction output remains derived material.
5. Epistemic classification applied to each observation or candidate.
6. Claims and observations assessed against their actual source spans and evidence.
7. Uncertainty, conflicts, missing information, and clarification questions identified.
8. Required caregiver review presented and durably recorded.
9. Accepted changes reconciled into the longitudinal care state.
10. A new, versioned care-state assessment is persisted.
11. Longitudinal retrieval reconstructs the state from persisted records, with source lineage and uncertainty.
12. Caregiver presentation distinguishes reported, observed, extracted, inferred, accepted, disputed, missing, and unknown information.
13. Corrections, withdrawals, late evidence, and changed review decisions trigger reassessment without rewriting source history.

## 2. Non-negotiable invariants

- **Source preservation:** Original input and uploaded bytes are immutable source material. Corrections never overwrite the original.
- **Identity:** Person and caregiver scope are derived from the authenticated server-side session. Caller-supplied identifiers never grant access.
- **Provenance:** Every derived observation, candidate, claim, assessment, question, and care-state revision must retain resolvable source references and attribution. Missing provenance is an explicit failure or unknown state, never silently repaired by guessing.
- **Epistemic separation:** A caregiver report is not a direct observation; extracted text is not a verified fact; a model output is not evidence; a pattern is not a causal explanation; a question is not a fact.
- **No silent promotion:** Extraction, Qwen, Companion, Community, and other generated output cannot independently write authoritative care facts. Only the workflow's explicit, authorized review rule may promote a candidate, and the review decision itself is persisted.
- **Uncertainty preservation:** Conflicting, ambiguous, negated, hedged, temporally unclear, incomplete, and unsupported information remains visibly qualified. Do not collapse unknown into false, absent, resolved, or normal.
- **Append-only history:** Source, review, correction, withdrawal, conflict-resolution, and decision/outcome history is retained. Current projections may change; historical events are not rewritten.
- **Person isolation:** Every source, derived record, review, question, assessment, retrieval result, export, and generated context is person-scoped and authorization-checked.
- **Deterministic core:** Core persistence, review, reconciliation, and retrieval continue to work when Qwen or another optional provider is unavailable.
- **Transaction ownership:** A caller owns the database transaction. Shared services flush but do not independently commit inside a larger workflow. Source write, accepted review, reconciliation, and resulting assessment must be atomic where the workflow claims one atomic operation.
- **Truthful presentation:** The UI renders persisted server state and its status. A successful source save must not be represented as completed extraction, review, or LCR integration.

## 3. Stage contract

| Stage | Required input | Required persisted output / invariant | Failure behavior |
|---|---|---|---|
| Input | Authenticated session, workflow type, payload | Validated request and server-derived scope | Reject invalid or unauthorized request |
| Source preservation | Original text, bytes, event, or external source reference | Immutable source record, content integrity metadata where applicable, author/reporter, source time, received time | Preserve source if later processing fails; surface incomplete processing |
| Evidence creation | Preserved source and attribution | Person-scoped evidence linked to its source and owning event/document | Roll back incomplete source/evidence transaction; never create orphaned evidence silently |
| Extraction | Persisted evidence ID and bounded source material | Versioned extraction result, method/runtime, status, source spans/quotes, quality metadata; model output clearly marked derived | Retry safely or persist explicit unavailable/failed state; never fabricate output |
| Epistemic classification | Extracted or directly reported material | Controlled status and attribution at observation/candidate level | Unknown/needs-review when classification cannot be established |
| Claim/observation assessment | Candidate, exact source span, evidence lineage, temporal scope | Assessment status, rationale, policy/version, source references and review requirements | Unsupported or mismatched claims remain unaccepted |
| Uncertainty/questions | Assessments, conflicts, gaps, temporal ambiguity | Person-scoped open questions/gaps with explicit links to source and subject/field where known | Ambiguous links stay unresolved; no inferred resolution |
| Caregiver review | Reviewable candidate and source context | Append-only decision, reviewer identity, answer/version, rationale, corrections and evidence links | No promotion without explicit valid acceptance; rejection and more-information decisions remain in history |
| Reconciliation | Persisted source changes and accepted decisions | Existing change/pattern/claim/gap engines run for the person; revisions and assessment snapshot are persisted | Atomic rollback for an atomic accepted-review workflow; otherwise explicit stale/failed processing state |
| Longitudinal retrieval | Authenticated person scope and as-of/current time | Reconstructed persisted state with bounded lineage, source links, time, uncertainty, conflict and truncation metadata | Retrieval errors are not converted to empty/normal state |
| Presentation | Persisted retrieval response | Caregiver-readable labels and visible uncertainty, review state, source and recency | Do not imply completion from a local optimistic update |
| Correction/reassessment | Authorized correction, withdrawal, late evidence or changed decision | New append-only history plus dependency-aware reassessment and revised projection | Preserve prior state/history; surface unresolved downstream impacts |

## 4. Workflow adapter rule

Workflow adapters may differ only in:

- Input format and validation.
- Source-preservation mechanism (text, Daily Care event, uploaded document, appointment artifact, handoff, or other supported source).
- Extraction mechanism and schema.
- Workflow-specific review authority and user experience.
- Domain-specific presentation.

They may not differ in identity enforcement, provenance requirements, epistemic vocabulary, no-silent-promotion rule, append-only review history, uncertainty handling, reconciliation semantics, longitudinal retrieval, correction semantics, or failure reporting.

A workflow is **enabled** only when its adapter invokes the shared lifecycle and passes the workflow acceptance tests below. Unsupported or unverified routes must be disabled or explicitly labelled unavailable; a UI route or model workflow ID alone does not make a workflow operational.

## 5. Shared execution result

Every workflow execution must return or expose a durable, person-scoped execution status that distinguishes at least:

- `source_preserved`
- `evidence_persisted`
- `extraction_pending`, `extraction_available`, `extraction_failed`, or `not_applicable`
- `assessment_pending`, `assessment_available`, or `assessment_failed`
- `review_required`, `review_completed`, or `review_not_required`
- `reconciliation_pending`, `reconciliation_completed`, or `reconciliation_failed`
- `retrieval_available` or `retrieval_unavailable`

These are stage states, not a single success boolean. A workflow must not report overall completion while a required stage is pending or failed. Existing persisted models should be reused where they can represent these facts accurately; introduce a migration or new execution record only after proving that no existing model can safely represent the required durable state.

## 6. Transaction and asynchronous processing

- Synchronous database stages must use one explicit transaction boundary owned by the API/workflow caller.
- A background model/extraction job must reference an already-persisted source/evidence ID, be idempotent, be person-scoped, and have durable queued/running/completed/failed state.
- A worker retry must not duplicate source records, review decisions, assertions, questions, or reconciliation revisions.
- Provider failure must not undo an already committed source save. It must leave a durable, visible processing failure or pending state that can be retried.
- If an accepted review and its required reconciliation cannot commit together, the review must not appear accepted while the LCR remains silently stale.
- Reconciliation must be safe to repeat and must not manufacture new facts from the same unchanged source on every retry.

## 7. Longitudinal read contract

The LCR and every caregiver-facing care summary must be built from persisted, authorized records, not from a transient model conversation or frontend cache. Reads must preserve:

- What is known and what is not known.
- Who reported or observed it and how it was obtained.
- The original source and exact supporting span where applicable.
- When it occurred, when it was recorded, and the time precision/uncertainty.
- Whether it is extracted, reported, accepted, disputed, corrected, withdrawn, or inferred.
- Conflicting evidence and unresolved questions.
- Historical/as-of state and the latest reassessment time.
- Truncation, unavailable sources, stale projections, and failed processing.

A missing source, unreadable document page, incomplete lineage traversal, or unavailable assessment must not be presented as proof that no relevant information exists.

## 8. Correction and dependency propagation

Corrections and withdrawals create new history. They must identify the superseded source/claim/observation/review where known and trigger reassessment of dependent claims, questions, patterns, decisions, summaries, appointment briefs, handoffs, and other derived projections. A derived artifact that cannot be safely recomputed must be marked stale or unavailable; it must not continue to appear current without qualification.

## 9. Required workflow acceptance suite

Each enabled workflow must demonstrate the following against the exact same current-main backend and frontend revisions:

1. Save source and read it back after a fresh database session.
2. Preserve original source bytes/text and verify integrity.
3. Persist evidence with correct reporter, collector, person, caregiver, and time attribution.
4. Reject cross-person and cross-caregiver access at every read, write, review, retrieval, and download boundary.
5. Extract from the persisted source, not an unpersisted browser payload.
6. Validate source quotes/spans and reject paraphrased or absent source support.
7. Preserve reported/direct-observation/extracted/inferred distinctions, negation, uncertainty and temporal ambiguity.
8. Persist questions and conflicts with explicit links; do not resolve ambiguous gaps.
9. Verify accept, correct, reject, and needs-more-information paths, including immutable review history.
10. Verify accepted changes create source-linked LCR records only under the required review policy.
11. Verify reconciliation and the new care-state assessment persist atomically and survive process restart.
12. Verify current and historical retrieval in a fresh session and after restart.
13. Verify the frontend renders the persisted state, review status, source and uncertainty without optimistic fabrication.
14. Verify correction, withdrawal, late evidence and review reversal trigger reassessment while preserving history.
15. Verify provider outage, worker retry, duplicate request, transaction failure and reconciliation failure behavior.
16. Verify idempotency under concurrent retries and no duplicate assertions, revisions, questions or decisions.

A unit test, mocked Qwen response, frontend build, route existence, migration file, or successful source-save response does not satisfy this end-to-end suite.

## 10. Implementation sequence on main

1. Inventory every mounted, frontend-reachable enabled workflow and trace its current source → evidence → extraction → review → reconciliation → retrieval path.
2. Identify existing shared services and persisted models; record every bypass and duplicate implementation.
3. Implement the shared backend execution coordinator around existing owners. Do not create duplicate tables or a parallel LCR.
4. Route one representative source path (Daily Capture) through the coordinator and prove the full persisted lifecycle.
5. Route document extraction/review, Companion proposals, appointment, handoff, and other enabled workflows through the same contract using thin adapters.
6. Add exact-main PostgreSQL, authorization, restart, idempotency, failure-injection and frontend integration tests.
7. Disable any workflow that cannot yet satisfy the contract; do not represent partial paths as complete.
8. Update the frontend to display stage-specific persisted state and caregiver review actions.
9. Update the capability matrix with evidence and exact commit/workflow links. No capability is complete based only on implementation presence.

## 11. Current known architectural risk

The current codebase already has useful shared components, including evidence lineage, capture review, document extraction/review, LCR context retrieval, and person-level change-intelligence reconciliation. However, the active main-branch baseline records multiple ingestion implementations and separately owned workflow lifecycles. Calling the same reconciliation function from several endpoints is not, by itself, a shared execution contract.

The next implementation must close the orchestration gap without flattening domain-specific source semantics or promoting model output to fact. The coordinator must own sequencing, stage status, transaction/failure rules and common invariants; existing domain services remain the owners of their specialised persistence and assessment logic.


## 12. Main-branch implementation ledger (2026-11-12)

### Implemented in source

The shared coordinator is `backend/app/services/care_continuity_execution.py`. Its current responsibility is deliberately narrower than the full lifecycle contract:

- Validates person-scoped persisted source events, evidence records (including multiple evidence IDs), Companion user messages/conversations, corrections, and care assertions.
- Verifies event/evidence linkage and caregiver ownership where the workflow requires same-caregiver authorship.
- Runs the existing person-level change-intelligence reconciliation inside the caller's transaction.
- Flushes but never commits or rolls back.
- Returns a stage receipt. The API changes reconciliation to `committed` only after its own commit succeeds.

The coordinator is now used by these backend paths:

- Structured Daily Capture and legacy Add Record (`/api/situation`).
- Direct evidence creation and evidence-source review.
- Daily Capture candidate review and document candidate review.
- Companion care-update acceptance.
- Care assertion acceptance and clarification-answer acceptance.
- Caregiver correction and reassessment.

The accepted-review paths that use the coordinator commit the review decision and reconciliation in the same transaction. The API surfaces a receipt to the frontend for Daily Capture, Add Record, Companion, and evidence-review responses. The clarification-review API returns a receipt, but its new frontend review controls were reverted after repeated Netlify build failures; the existing UI still saves answers separately and does not yet expose the review action. Correction receipts are returned by the backend response contract, but the caregiver-facing correction UI has not yet been verified to display them.

### Not yet implemented or proven

This ledger is not a claim that the full 13-stage contract is complete.

1. The coordinator currently owns common source validation and the reconciliation boundary. Extraction, epistemic classification, source-span assessment, question generation, and caregiver-review policy remain in their existing domain adapters; they are not yet executed by one generic orchestrator.
2. There is no generic durable execution ledger for every stage, workflow, retry, and worker attempt. Current receipts are returned by the individual API calls; they are not a universal persisted execution record.
3. Appointment workspace and handoff workflows have not yet been routed through this coordinator. Their snapshots, consent, and versioning remain specialised. They must be audited to establish whether they are read-only derived artifacts or whether any of their user inputs need to enter the LCR as source evidence.
4. Correction dependency propagation and stale-artifact invalidation still need end-to-end verification across summaries, appointments, handoffs, and other derived views.
5. The new coordinator tests have been authored but have not been executed in a confirmed PostgreSQL test run. Unit tests, live cross-person denial, process restart, idempotent retry, and transaction-failure tests remain required.
6. The Railway production service is configured with `watchPatterns: ["MANUAL_DEPLOY_ONLY"]`. Commits to backend `main` do not automatically deploy this service. The live deployment observed during this implementation predates these commits. No production deployment or migration was initiated as part of this work.

### Required next actions

- Run the backend test suite against PostgreSQL, fix all failures, and add transaction-failure and retry tests for the newly routed review paths.
- Route appointment and handoff workflows only after classifying each as source ingestion, reviewable derived artifact, or read-only output; never promote workspace text or handoff summaries to care facts by default.
- Build the generic persisted execution ledger only after confirming that existing models cannot represent the full stage/retry lifecycle without overloading unrelated records.
- Complete the end-to-end source-to-LCR test matrix before describing the shared contract as complete.
