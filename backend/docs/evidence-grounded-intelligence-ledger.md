# SolenOS Evidence-Grounded Intelligence — Implementation Ledger

**Scope:** canonical `main` only. Pull requests are not modified.  
**Purpose:** a verification ledger, not a declaration that the entire capability map is complete.  
**Last audited:** 2026-10-29. Latest direct-main changes bind safety evaluation references to persisted same-person evidence, replace caller-supplied source text with the stored original, clear caller-supplied review/confidence flags, validate source spans against persisted text, and scope safety response validation/audit history to the bearer session. Structured-observation tests now supply explicit source offsets. Backend tests passed on implementation commit [`45179ef`](https://github.com/getmycareos-star/SolenOS_backend/commit/45179ef1cbcc7ce996f474d3b55f91e63b415bda) in [run 40875008698](https://github.com/getmycareos-star/SolenOS_backend/actions/runs/40875008698); PostgreSQL migration validation also passed in that run. The separate backend Docker-build smoke workflow is still running. This ledger deliberately does not mark the entire capability map complete.  
**Rule:** a capability is not marked complete merely because a model, helper, endpoint, UI panel, or unit test exists. Completion requires persistence, authorization, migrations, real workflow integration, and relevant automated verification.

## Status definitions

- **PARTIAL** — concrete implementation exists, but at least one required behavior or end-to-end proof is missing.
- **UNVERIFIED** — code or tests exist, but current-main execution evidence is not yet available.
- **MISSING** — no adequate implementation was found during this audit.
- **BLOCKED** — requires a real dependency, policy, source, or authorization decision that is not available.

No group is marked fully complete in this ledger. Source-lineage persistence and PostgreSQL migration checks have passed on the cited implementation commit, but automated source-family discovery and corroboration assessment remain partial.

## Capability groups A–R

### A. Evidence identity and provenance — PARTIAL
- [x] Existing evidence identity, uploader identity, source type, original source text, upload time, source spans, and care-record ownership fields exist in the backend model.
- [x] Distinguish uploader from author/reporter/collector in the data model: `original_author`, `reported_by_label`, `reported_by_caregiver_id`, `collected_by_caregiver_id`.
- [x] Add collection time, event time, original timezone, origin channel, source reference, parent evidence link, ingestion time, and SHA-256 of the exact stored UTF-8 source text.
- [x] Add separately typed `evidence_kind` and `epistemic_status` fields; unsupported classifications are rejected, and omitted values remain unclassified rather than inferred.
- [x] Bind evidence creation to the server-issued bearer session and reject cross-record identities.
- [x] Add an append-only ORM guard and database trigger preventing mutation of original evidence source text and its stored fingerprint; expose a person-scoped integrity endpoint that recomputes the exact UTF-8 text hash.
- [ ] Verify migration upgrade against fresh and pre-existing PostgreSQL schemas, direct-SQL mutation rejection, and create/retrieve/hash/classification behavior on the exact current `main` commit in CI.
- [x] Original uploaded-file bytes, SHA-256, size, filename, and MIME type are persisted in `evidence_uploads`; download verifies byte count and recomputed SHA-256 before returning content.
- [ ] Complete device identity, import/transfer lineage, and explicit evidence links to every event, claim, observation, decision, outcome, and review type; the source-text hash and uploaded-binary hash remain distinct integrity domains.
- [x] Extend evidence acquisition provenance with reported-at time, collection method, privacy-minimizing opaque device label, transfer source/reference, and an optional client idempotency key backed by a per-person unique constraint and server-computed canonical request fingerprint. These are persisted fields; end-to-end verification remains pending.

### B. Evidence and knowledge classification — PARTIAL
- [x] Existing source type, event/assertion kind, evidence status, and epistemic-state structures distinguish several source and knowledge types.
- [x] Add a schema-constrained evidence-kind vocabulary for direct observation, caregiver report, professional observation, person's own report, documented record, instrument measurement, photo/video, audio/transcript, imported information, historical recollection, derived observation, inference, and hypothesis.
- [x] Keep epistemic status separate from evidence kind, with explicit unverified, uncertain, unknown, missing, disputed, superseded, and withdrawn values.
- [ ] Extend and validate the vocabulary against every historical source type and all ingest adapters; status values remain optional and are not auto-inferred.
- [ ] Verify every ingestion and presentation path preserves these distinctions without collapsing them into a generic verified label.

### C. Source reliability and evidence quality — PARTIAL
- [x] Persisted evidence review records retain review type, outcome, rationale, criteria, policy version, source-attribution snapshot, reviewer identity, and timestamp.
- [x] Review history is append-only in the ORM and has a PostgreSQL migration trigger.
- [x] Unsupported numeric confidence is rejected in uncertainty assessments; frontend provenance no longer maps extraction scores through the arbitrary 0.85/0.65 thresholds.
- [ ] Add evidence-backed authenticity/tampering checks, extraction-quality evidence, measurement method/units/calibration, and reviewer-authority policy.
- [x] Safety evaluation now discards caller-provided authority, quality, currentness, confidence, support, and contradiction flags and uses the persisted source text for each same-person evidence ID; submitted source spans survive only if they match the stored text.
- [ ] No confidence calibration is available until a task-specific method has validation evidence.

### D. Claim-level evidence grounding — PARTIAL
- [x] Existing care facts, care assertions, evidence links, source spans, and persisted assertion statuses provide claim-related primitives.
- [x] Evidence integrity endpoint returns linked evidence and duplicate-content candidates.
- [x] Add an append-only, versioned claim assessment API for all ten explicit statuses; each assessment records the reviewer from the server-authorized session, rationale, policy version, linked evidence relationships, and superseded assessment ID.
- [x] Validate source passages as exact substrings of linked evidence and require explicit supporting/contradicting relationships for supported, partially supported, contradicted, and conflicting statuses.
- [x] Add an append-only, same-person claim dependency API with explicit dependency types, rationale, server-derived reviewer, duplicate rejection, claim existence validation, and cycle prevention.
- [x] Expose dependency/dependent graph reads with a limitation statement that declared links do not prove semantic entailment or truth.
- [x] Connect dependency creation and graph inspection to the LCR claim-assessment panel with authenticated session scope and failure-safe UI messaging.
- [x] Add backend persistence/isolation/cycle regression coverage and a frontend contract assertion for the connected workflow.
- [ ] Full dependency-aware invalidation/re-review triggers, cross-claim supersession lifecycle, correction/withdrawal workflows, and exact-main CI verification remain outstanding.

### E. Corroboration and source independence — PARTIAL
- [x] Evidence integrity endpoint detects identical normalized source text and explicitly warns against counting duplicates as independent corroboration.
- [x] Evidence review captures an explicit independence-review criterion and preserves the reviewer/policy record.
- [x] Add person-scoped persisted source relationship records for same-origin, copied-from, forwarded-from, derived-from, corroborates, contradicts, duplicate-candidate, and related-to links.
- [x] Record explicit independence states (independent/shared-origin/unknown/not-assessed), rationale, reviewer identity from the authorized session, optional origin group, and append-only timestamps.
- [x] Reject cross-record relationships, self-links, duplicate links, false independent labels on lineage edges, and source-lineage cycles; list relationships from either endpoint.
- [x] Add migration, unique constraints, PostgreSQL/SQLite append-only guards, API persistence/isolation tests, and migration-workflow checks.
- [x] Add API regression coverage for persisted source lineage, server-derived reviewer identity, false-independence rejection, cross-record relationship denial, and reverse endpoint listing. (Test added to `backend/tests/test_uncertainty.py`; current-main CI result pending.)
- [ ] Current-main CI and deployed PostgreSQL execution must pass before this capability is considered verified.
- [ ] Add automated source-family clustering for copied/forwarded content and claim/event/observation-level corroboration assessments; human-declared lineage is not automatic source discovery.
- [ ] Distinguish no corroboration from attempted-but-failed corroboration in a canonical state model.

### F. Conflict detection, comparison, and resolution — PARTIAL
- [x] Persisted contradictions include context, event, effective interval, matching candidate IDs, evidence IDs, and resolution status.
- [x] Conflict resolution/reopening transitions have append-only audit records.
- [x] Matching considers context and explicit time overlap; invalid intervals and creation of already-resolved conflicts are rejected.
- [x] Full and partial conflict resolution require linked same-record evidence and a non-empty rationale; partial resolution additionally requires an explicit detail-level scope. Scope and evidence are retained in immutable transition history.
- [x] Add caregiver-facing LCR conflict review, evidence selection, scoped partial resolution, and history inspection with explicit failed-read/write messaging.
- [ ] Complete attribute/value/unit comparison, automated partial-contradiction matching, resolution authority matrix, explicit unresolved remainder model, recurrence detection, and conflict notifications across all related workflows.
- [ ] No automatic resolution by timestamp or majority vote.

### G. Structured observation extraction — PARTIAL
- [x] Existing ingestion/extraction and care-assertion paths preserve source text, evidence IDs, source spans, and separate event/recording times in supported workflows.
- [x] Add append-only typed observations linked to an exact passage and offsets in original evidence, with source kind, epistemic status, subject, attribute/value/units/context, negation, hedging, ambiguity, observer/method, extraction method/version, and separate occurrence/observation/report/recording times.
- [x] Validate exact source passages, ambiguous repeated passage offsets, observed-status requirements, unknown/missing value semantics, time-zone-bearing timestamps, and same-record observer identity.
- [x] Preserve observation corrections as new append-only versions with a same-evidence supersession link and mandatory correction rationale.
- [ ] Universal automated extraction across document/audio/transcript/image/text adapters and pronoun/entity resolution remain incomplete.
- [x] Add API regression coverage for exact source-passage enforcement, source offsets, report-versus-observed status preservation, append-only correction linkage, and ORM mutation rejection. (Test added to `backend/tests/test_uncertainty.py`; current-main CI result pending.)
- [ ] Verify document, audio, transcript, image, and text paths preserve originals and report unsupported formats/failures.

### H. Fact, inference, interpretation, and reported-versus-observed separation — PARTIAL
- [x] Uncertainty analysis distinguishes unknown, conflicting, ambiguous, partially known, insufficient, and uncalibrated states in supported tasks.
- [x] Unsupported assessment tasks now fail closed to unknown.
- [x] Structured observation API refuses to label an observation as observed without a direct-observation kind, named observer, and method; reported/inferred/hypothesis/unknown/missing remain explicit statuses.
- [ ] Verify end-to-end separation of behavior vs interpretation, prediction vs outcome, correlation vs causation, and question vs assertion across Companion, summaries, appointments, and handoffs.
- [ ] Add dependency-aware inference invalidation and grounded alternative explanations; no diagnosis or causal claim from narratives or temporal sequence alone.

### I. Temporal evidence and historical continuity — PARTIAL
- [x] Care events preserve occurrence time, recorded time, timezone, and time provenance; evidence now has separate occurred, collected, uploaded, and ingested time fields.
- [x] Conflict matching checks explicit intervals and rejects reversed intervals.
- [x] Structured observations retain distinct occurred, observed, reported, and recorded times; submitted timestamps must be timezone-aware.
- [ ] Complete review/correction/effective interval dimensions, approximate time precision, late/backdated evidence policy, domain-specific freshness, temporal claim validity, and historical snapshot reconstruction.

### J. Duplicate detection, entity resolution, and identity integrity — PARTIAL
- [x] Existing evidence integrity logic identifies exact normalized-text duplicate candidates without deleting records.
- [x] Evidence source text now has a deterministic SHA-256 fingerprint field for exact stored text.
- [x] Add bounded normalized-text near-duplicate candidates (minimum text length, explicit algorithm version, comparison cap, and no confidence score); candidate output explicitly does not establish copying, common origin, truth, or independence. Frontend LCR now displays candidate groups and analysis limitations. Current-main CI verification is pending.
- [ ] Document-version/semantic candidates, event identity resolution, authorized person/entity merge proposals, and false-match review remain missing.
- [x] Add an optional per-person evidence ingestion key. An exact retry returns the existing record; reuse with a different canonical request payload returns HTTP 409; concurrent duplicate keys are handled through the database uniqueness constraint. Current-main API and PostgreSQL migration verification is pending.
- [ ] Do not destructively merge people or evidence solely on similarity.

### K. Evidence completeness, freshness, and gaps — PARTIAL
- [x] Persisted review criteria include completeness and freshness; information gaps and open questions are modeled and exposed through APIs.
- [x] Unknown, missing, and unassessed are represented in existing epistemic workflows.
- [ ] Add versioned domain-specific policies, rationale-backed review intervals, evidence dependency sufficiency per decision, stale/unknown/unassessed separation across every consumer, and change-triggered reassessment.

### L. Evidence-grounded change detection — PARTIAL
- [x] Persisted change assessments and patterns exist; evidence references, comparison windows, confirmation history, and reversal/supersession concepts exist.
- [ ] Verify all requested dimensions: value, frequency, duration, context, function, routine, care/support need, reporting-rate change, persistence, reversal, and sparse-evidence uncertainty.
- [ ] Prove no change is claimed without comparable baseline and current observation, and distinguish recording-frequency changes from event-frequency changes.

### M. Evidence-grounded care decisions and outcomes — PARTIAL
- [x] Decision, action-outcome, follow-up, and handoff data structures exist.
- [ ] Link each decision to the evidence available at decision time, alternatives, unresolved questions, authorization, action completion evidence, outcome assessment method, expected/observed outcome, and follow-up/reversal history.
- [ ] Keep temporal association separate from causal attribution.

### N. Evidence-grounded questions and next steps — PARTIAL
- [x] Open questions and information gaps have persisted records and lifecycle fields.
- [x] Add a caregiver-facing LCR workflow that creates a clarification question tied to a selected source-attributed claim and its subject context, with optional same-record evidence links and explicit priority.
- [x] Add a normalized `open_questions.claim_id` foreign key to `care_assertions`; creation validates both same-person ownership and matching subject scope, and the UI submits/displays the normalized link instead of relying on context JSON alone.
- [x] Capture intended answerer role/optional label and an explicit necessary/optional/not-assessed state; necessary/optional judgments require a rationale, and answerer roles are validated rather than inferred from wording.
- [x] Attribute each recorded answer to the session caregiver and preserve every answer version, provenance text, and timestamp in append-only history; expose a person-scoped answer-history API and reject cross-record history reads.
- [x] Add SQLite ORM guards and a PostgreSQL append-only trigger for answer-history rows; migration workflow verifies the table and trigger.
- [ ] Generate questions automatically only from traceable evidence gaps, track intended answerer/necessity, and avoid repeating questions already answered by sufficient current evidence.
- [ ] An answer must not automatically resolve the underlying claim without assessment.

### O. Integrity, security, and governance — PARTIAL
- [x] Evidence creation now requires a server-issued bearer session matching person and uploader caregiver.
- [x] Reporter/collector caregiver references and parent evidence are validated against the same care record.
- [x] Evidence reviews, claim assessments, source relationships, structured observations, claim dependencies, conflict-resolution events, and clarification-answer events are append-only in ORM; PostgreSQL/SQLite migrations add database-level mutation guards for these audit records.
- [x] Claim-assessment writes derive person and reviewer identity from the server-validated bearer session, validate every linked evidence ID against that same person, and preserve assessment history with ORM/database append-only guards.
- [x] Safety response validation rejects duplicate, missing, and cross-record evidence IDs; safety audit history is scoped to the authenticated record and bounded to 200 rows.
- [ ] Audit every remaining evidence/claim/conflict/gap/question endpoint for scope authorization, every foreign ID, transactional rollback, idempotency, secure media handling, retention/deletion, backup/restore, and production failure behavior.
- [ ] Verify immutable source handling against all writers, direct SQL, and deployed PostgreSQL behavior.

### P. Grounded AI reasoning and explanations — PARTIAL
- [x] Existing uncertainty and trust-provenance layers represent retrieval scope, missing evidence, limitations, and non-calibrated confidence in supported flows.
- [x] Safety evaluation references are rebound to persisted source text in the authorized care record; invalid/foreign evidence IDs fail closed, and client-submitted source spans are discarded when they do not match persisted text.
- [ ] Validate every factual output claim against persisted claim/evidence links, freshness, conflicts, and source independence across all consumers.
- [ ] Ensure citation integrity, no fabricated quotations, dependency-aware invalidation, and safe abstention across companion, summaries, appointments, LCR, and handoffs.

### Q. Frontend evidence workflows — PARTIAL
- [x] The persisted EvidenceReviewPanel is connected to current-care understanding and displays the backend-computed source-text hash status (match/mismatch/unavailable), exact hash scope, and explicit limitation that the hash does not authenticate the original binary.
- [x] Connect source lineage and corroboration review to the UI with explicit independence state and failure-not-success messaging.
- [x] Connect structured observation capture/history to the UI, including exact source passage, epistemic status, negation, hedging, ambiguity, and justified correction history.
- [x] Add a caregiver-facing claim assessment panel in the LCR with all ten explicit statuses, source-link relationship controls, exact-passage input, rationale, and persisted assessment history.
- [x] Current-care UI already exposes evidence-integrity inspection, duplicate candidates, interpretation limits, and review coverage.
- [x] Frontend verification asserts the LCR connection and guards against reintroducing arbitrary confidence thresholds.
- [x] Add a caregiver-facing conflict-resolution panel with scoped partial resolution, selected evidence references, and append-only transition history; failures are not displayed as successful saves.
- [ ] Complete broader source/claim detail navigation, full provenance identities/times across all surfaces, correction/retraction UI, clarification workflow, and accessible error/retry/rollback states across every consuming workflow.

### R. Testing, verification, and operations — UNVERIFIED
- [x] Existing backend tests cover evidence-review persistence, cross-record denial, append-only history, contradiction scope, and uncertainty behavior.
- [x] PostgreSQL migration workflow validates a fresh schema, migration head, core table/column contracts, and several identity/persistence workflows.
- [x] Added tests for evidence provenance fields, source-text hashing, and evidence-create authorization.
- [x] Add source-lineage API tests for create/list persistence, cross-record denial, duplicate prevention, and cycle prevention.
- [ ] Current-main test and PostgreSQL migration runs must pass before these latest additions are considered verified.
- [x] Add tests for source relationship persistence/isolation/cycle rejection/duplicate rejection and exact-source-span observation persistence, classification boundaries, invalid source passages, and cross-record denial.
- [ ] Current-main backend tests and PostgreSQL migration validation must pass before these additions are considered verified; broader E2E/property/concurrency/failure tests remain incomplete.
- [x] Add route regressions for rejecting unpersisted safety evidence IDs and cross-record audit reads, plus a sanitizer regression proving caller-supplied source text is replaced by persisted source text. The exact latest-main run remains pending.
- [x] Add unit tests for bounded near-duplicate candidate semantics, short-text exclusion, explicit non-independence interpretation, and fail-closed behavior above the comparison cap; these are unit-level tests, not proof of the full HTTP/PostgreSQL workflow.

## Latest direct-main increment — 2026-10-29

- Backend [fix SQLAlchemy 2 evidence-scope query](https://github.com/getmycareos-star/SolenOS_backend/commit/4ee32cdb3a30759085aad5a44176482bb1c5d4cd) addresses the observed `Query.scalars()` failure in the situation assessment. Rerun proof was pending at the time of this update.
- Backend [persist acquisition provenance](https://github.com/getmycareos-star/SolenOS_backend/commit/326919315219b32ae49d396436051a911e8a0bb2), [add acquisition migration](https://github.com/getmycareos-star/SolenOS_backend/commit/b2b11612c698d4327768cdad98b5d63d5a1471af), [expose the idempotency key](https://github.com/getmycareos-star/SolenOS_backend/commit/a738f015fe0bde52d1dc7e7db4c8b55747104cc1), [add the idempotency migration](https://github.com/getmycareos-star/SolenOS_backend/commit/b0e868383caff421ff4e0ef34ebb87d0527fd03f), and [bind ingestion retries to a canonical request fingerprint](https://github.com/getmycareos-star/SolenOS_backend/commit/bd512e5cee7624d38f833f1af74e5919ca860cad).
- Backend [add idempotency regression tests](https://github.com/getmycareos-star/SolenOS_backend/commit/3b07785082c8db1a41709cc1ac7e910fb7ff335c) and [verify migration schema](https://github.com/getmycareos-star/SolenOS_backend/commit/4fbe6f8d2f22728e9ff60382fa652efcdad675a3).
- Frontend [show acquisition provenance in LCR](https://github.com/getmycareos-star/SolenOS-landing/commit/66c8243f7e957dd640a4581d330db5f6f3b1e5e8) and [guard it with a frontend contract assertion](https://github.com/getmycareos-star/SolenOS-landing/commit/f4d7af1038fb2761886c2181ac628e1e0b49d5fd).
- Migration revisions `20261029_18_acq_provenance` and `20261029_19_evidence_idem` extend the canonical chain, followed by open-loop revisions `20261029_20_open_loops` and `20261029_21_open_loop_occurrences`. PostgreSQL run [40878315698](https://github.com/getmycareos-star/SolenOS_backend/actions/runs/40878315698) exposed that the historical metadata-reconciliation migration can pre-create the open-loop tables; migration 20 was updated to guard existing tables and indexes without SQLite table rebuilds that could remove immutability triggers. The corrected latest-main PostgreSQL run is still pending.

## Current implementation commits

### Frontend
- [LCR evidence review integration](https://github.com/getmycareos-star/SolenOS-landing/commit/499232feaa22adfbe77fbcd292d44b8492235844)
- [Show evidence provenance and temporal dimensions in review UI](https://github.com/getmycareos-star/SolenOS-landing/commit/0707079fc666ed34ff03bec666e38c3575c498bb)
- [Verify evidence classification presentation](https://github.com/getmycareos-star/SolenOS-landing/commit/c550b29afef1966dd4c5f1d9a1ae401f264c51f6)
- [Nullable provenance confidence](https://github.com/getmycareos-star/SolenOS-landing/commit/e3f54c895adcbce13e3d53fa84439d5215d82c4f)
- [Remove arbitrary confidence thresholds](https://github.com/getmycareos-star/SolenOS-landing/commit/b95dfe82045b7c8b547b06230d54c9d976f635f4)
- [Add regression assertions](https://github.com/getmycareos-star/SolenOS-landing/commit/716d5ce4330a6234ebbec61459204a250d3efb15)

### Backend
- [Persist distinct provenance fields and source hash](https://github.com/getmycareos-star/SolenOS_backend/commit/d5e5aa40ca512c0fae152b892d625a6f53d5d896)
- [Expose provenance fields in schemas](https://github.com/getmycareos-star/SolenOS_backend/commit/eaa2f6d31b602bd8a8b7b5d8feb98a4872870a67)
- [Validate provenance references](https://github.com/getmycareos-star/SolenOS_backend/commit/f450b169466e94d9659e6561b6319f4baad8a957)
- [Add provenance migration](https://github.com/getmycareos-star/SolenOS_backend/commit/a5a2764141ba8a8a41020f3eae6bcaa14e2d5ddf)
- [Add explicit evidence-kind and epistemic-status migration](https://github.com/getmycareos-star/SolenOS_backend/commit/c104ee04033738d13776f8850d98d7f97f6eefb6)
- [Add migration and schema validation](https://github.com/getmycareos-star/SolenOS_backend/commit/4e65f66677caec65ed03ea377d4ebbfd635e3faa)
- [Require authenticated evidence creation](https://github.com/getmycareos-star/SolenOS_backend/commit/9e92221a7c08098eb98a883d5d77ca11446ad0cf)
- [Add provenance and authorization tests](https://github.com/getmycareos-star/SolenOS_backend/commit/95eb8bdff2143ed300cc9d1d7e89a5ad7e8bbd61)
- [Test explicit evidence kind and epistemic status](https://github.com/getmycareos-star/SolenOS_backend/commit/c52cfad9815d2bf909bd2988080718b4cd46b6dc)

## Verification links

- [Latest backend tests](https://github.com/getmycareos-star/SolenOS_backend/actions/workflows/backend-tests.yml?query=branch%3Amain)
- [Backend validation](https://github.com/getmycareos-star/SolenOS_backend/actions/workflows/backend-validation.yml?query=branch%3Amain)
- [PostgreSQL migration validation](https://github.com/getmycareos-star/SolenOS_backend/actions/workflows/postgres-migration-validation.yml?query=branch%3Amain)
- [Frontend validation](https://github.com/getmycareos-star/SolenOS-landing/actions/workflows/frontend-validation.yml?query=branch%3Amain)
- [Frontend checks](https://github.com/getmycareos-star/SolenOS-landing/actions/workflows/frontend-checks.yml?query=branch%3Amain)

**Important:** This ledger is intentionally not a completion certificate. Items above marked partial or unverified still require implementation and/or evidence from current-main runs.

### S. Whole-situation assessment and longitudinal projection — PARTIAL
- [x] Build a person-scoped assessment from bounded persisted LCR sources; the projection is explicitly read-only and does not promote claims into verified facts.
- [x] Expose source coverage, unavailable/missing sources, linked evidence counts, explicit non-calibrated confidence, and limitations.
- [x] Project persisted unresolved conflicts, reconciliations, uncertainties, information gaps, unanswered questions, follow-up links, and flagged care events into open loops without inventing owners, deadlines, or outcomes.
- [x] Persist immutable, person-scoped assessment snapshots; deduplicate semantically unchanged reassessments; retain history and return a projection diff when persisted records change.
- [x] Add a bounded source-linked situation trajectory from persisted timeline, events, facts, observations, caregiver contexts, decisions, and outcomes. Event time and record time are kept distinct; entries with no recorded time remain unanchored.
- [x] Explicitly label the trajectory as chronology only; temporal ordering does not establish causation.
- [ ] Current-main CI must pass for the trajectory test and current assessment code before this change is verified.
- [ ] Formal person-specific baseline model, robust comparable-observation change metrics, persistence/reversal classification, trajectory gap detection, and historical state-at-time reconstruction remain incomplete.
- [ ] Capability metadata is coverage reporting, not proof that every listed domain has a complete reasoning engine.

### T. Caregiver-facing situation orientation — PARTIAL
- [x] Workspace orientation requests a fresh backend reassessment on entry; the backend is responsible for semantic deduplication.
- [x] The UI displays persisted record coverage, open-loop count, a bounded recent trajectory, epistemic status, recorded temporal basis, and a no-causation warning.
- [x] Situation-assessment retrieval failure does not suppress the existing saved-care view; the assessment panel is omitted when unavailable rather than fabricated.
- [ ] Current-main frontend build and test evidence is pending for the latest UI change.
- [ ] Add source-detail navigation from each trajectory item, explicit current-versus-baseline comparison, assessment history/diff UI, accessible open-loop lifecycle actions, and end-to-end browser/API verification.

### U. Current implementation evidence added in this work — UNVERIFIED until current-main CI completes
- [x] Backend implementation commit: [evidence-linked situation trajectory projection](https://github.com/getmycareos-star/SolenOS_backend/commit/734ea571d94a9eb54c897206555c83b13717c31c).
- [x] Backend regression-test commit: [verify persisted trajectory and causal boundary](https://github.com/getmycareos-star/SolenOS_backend/commit/92a79e54ae8612db2aaa6f6a17b0e4c1fa846c4b).
- [x] Frontend UI commit: [show persisted trajectory and open loops](https://github.com/getmycareos-star/SolenOS-landing/commit/a3a9eccfa9e31837d4eae69d230d9ea3c706c9f2).
- [x] Frontend freshness commit: [reassess persisted evidence on workspace entry](https://github.com/getmycareos-star/SolenOS-landing/commit/69d13cac9e54a8614b99234d254be7acff1c27d5).
- [ ] Passing CI for the exact latest backend and frontend commits is required before marking these additions verified.


### V. Claim-level evidence assessment and source-text immutability — PARTIAL pending exact-current-main CI
- [x] Backend migration: [versioned evidence claim assessments](https://github.com/getmycareos-star/SolenOS_backend/commit/046bdd446873493f237b6304c3dad9bbcf1025cf).
- [x] Backend API: [scoped claim assessment lifecycle](https://github.com/getmycareos-star/SolenOS_backend/commit/58fbe80cf06b4bbaad7d08d1425c386548afa15d).
- [x] Backend tests: [source passage, assessment versioning, and person isolation](https://github.com/getmycareos-star/SolenOS_backend/commit/8c4bf774f6c35ab8290575569df67aa8bd1109b6).
- [x] Frontend: [claim assessment panel](https://github.com/getmycareos-star/SolenOS-landing/commit/a97c9036d0eb223366bb0e0ce7b48cd69a4ae934), [LCR integration](https://github.com/getmycareos-star/SolenOS-landing/commit/a593937cdcc31275004474fbfd1a91a35d18d138), and [regression contract](https://github.com/getmycareos-star/SolenOS-landing/commit/70bfae5b19b70ac1afffa2f100e6bbebf22a867e).
- [x] Frontend checks passed on the panel commit and LCR integration commit; frontend validation passed on the contract commit.
- [ ] Earlier backend CI exposed two real issues: the migration graph test expected the previous head, and legacy migration setup pre-created the assessment table. The head assertion and migration were corrected; re-run and inspect current-main backend test plus PostgreSQL migration jobs before treating this subsystem as verified.
- [ ] Claim lifecycle still lacks a complete cross-claim dependency graph, a target claim link for supersession, full conflict-scope resolution, and full end-to-end browser/API test evidence.


## Latest main-only hardening pass — 2026-10-29

This pass made targeted fixes after inspecting the actual repository tree and workflow definitions. These are implemented on `main`; no pull request was changed.

- Corrected `backend/tests/test_migration_graph.py` to require the latest connected Alembic head, `20261029_11_cov_prov`, instead of the stale `20261029_09_obs` head. The test also asserts the coverage immutability and change-coverage provenance revisions are reachable from the graph.
- Updated `.github/workflows/postgres-migration-validation.yml` to assert the same current head in both fresh-schema and legacy-backfill upgrade paths.
- Corrected the frontend type-check blocker in `src/components/mvp-workspace/ChangeIntelligencePanel.tsx`: a duplicate block-scoped `detail` declaration prevented TypeScript validation before any evidence-specific checks could run.
- Hardened `src/components/EvidenceReviewPanel.tsx` to reject an LCR-provided person scope that differs from the authenticated anonymous session scope. Added a regression assertion to `scripts/verify-evidence-grounding-contract.mts`.

### Verification state for this pass

- Backend migration-head correction commit: [449f427](https://github.com/getmycareos-star/SolenOS_backend/commit/449f4272074f72fb9a0db5b17c620c6a835337ea)
- PostgreSQL workflow head correction commit: [07b3afd](https://github.com/getmycareos-star/SolenOS_backend/commit/07b3afd449ffabb9f1702f2602bda48f8e4ca078)
- Frontend LCR scope guard commit: [a316c6a](https://github.com/getmycareos-star/SolenOS-landing/commit/a316c6aa4de090e80f6130d065d0de3976dae57e)
- Frontend scope regression assertion commit: [d950003](https://github.com/getmycareos-star/SolenOS-landing/commit/d950003f2d97b6807f37afae5a01486a38a6841f)
- Frontend TypeScript duplicate-declaration fix: [d3862b9](https://github.com/getmycareos-star/SolenOS-landing/commit/d3862b9e5f581892b66a69246e39920c8255378d)
- The prior frontend run at commit `3a05118` failed at TypeScript checking because of the duplicate declaration above. The fix is committed, but its new workflow run has not yet completed.
- At the time of this update, backend test/validation and PostgreSQL migration workflows are queued; no successful current-head result is claimed.
- The source-integrity hash covers the stored UTF-8 source text only. It does not authenticate the original document, photo, audio, or binary file.
- The 18 capability groups remain **PARTIAL or UNVERIFIED** until their individual gaps and end-to-end proof listed above are closed. This ledger intentionally does not claim complete implementation.



### Additional CI-driven migration corrections

The PostgreSQL workflow exposed two concrete migration defects, which were corrected on `main`:

- The change-coverage migration attempted to add columns that already existed in some schema paths. It now checks for the `changes` table and adds only missing columns, preserving existing data.
- The first revision identifier exceeded Alembic's default PostgreSQL `alembic_version.version_num VARCHAR(32)` limit. The canonical revision ID is now `20261029_11_cov_prov` (20 characters), and the migration graph test and PostgreSQL workflow use that exact ID.

Commits:
- [Idempotent coverage-provenance migration](https://github.com/getmycareos-star/SolenOS_backend/commit/c42b1b308d958a5aaf8d30fa81454f9c15a4dfff)
- [Compact Alembic revision ID](https://github.com/getmycareos-star/SolenOS_backend/commit/dbded0b7e9c32162c59f03861c20dbba2a20bbd5)
- [Migration graph assertion update](https://github.com/getmycareos-star/SolenOS_backend/commit/cc87967032709362456e05defdcad41cb7f68c0a)
- [PostgreSQL workflow revision update](https://github.com/getmycareos-star/SolenOS_backend/commit/dd66a1a5fb906e1010f123f545bc794125f3450a)

The failing run that revealed these defects is [PostgreSQL migration validation run 40859766335](https://github.com/getmycareos-star/SolenOS_backend/actions/runs/40859766335). A corrected PostgreSQL migration validation run succeeded at [run 40859971827](https://github.com/getmycareos-star/SolenOS_backend/actions/runs/40859971827), commit [dd66a1a](https://github.com/getmycareos-star/SolenOS_backend/commit/dd66a1a5fb906e1010f123f545bc794125f3450a). This verifies that workflow's fresh PostgreSQL upgrade and subsequent migration checks for that commit; backend test-suite success remains separate and pending.


### Frontend verification and deployment boundary

- Frontend checks succeeded on current frontend `main` commit [d3862b9](https://github.com/getmycareos-star/SolenOS-landing/commit/d3862b9e5f581892b66a69246e39920c8255378d): [run 40859533079](https://github.com/getmycareos-star/SolenOS-landing/actions/runs/40859533079).
- Frontend validation (TypeScript, production Next.js build, and configured continuity/safety checks) succeeded on the same commit: [run 40859533053](https://github.com/getmycareos-star/SolenOS-landing/actions/runs/40859533053).
- Netlify deployment did **not** happen: [run 40859533059](https://github.com/getmycareos-star/SolenOS-landing/actions/runs/40859533059) stopped because the required Netlify credentials are not configured. This is a deployment blocker, not a successful deployment.
- The latest backend test suite and validation runs for the documentation-updated current backend `main` are still queued at this point; do not infer they passed from the PostgreSQL migration workflow.

