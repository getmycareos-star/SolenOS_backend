# SolenOS Evidence-Grounded Intelligence — Implementation Ledger

**Scope:** canonical `main` only. Pull requests are not modified.  
**Purpose:** a verification ledger, not a declaration that the entire capability map is complete.  
**Last audited:** 2026-10-29.  
**Rule:** a capability is not marked complete merely because a model, helper, endpoint, UI panel, or unit test exists. Completion requires persistence, authorization, migrations, real workflow integration, and relevant automated verification.

## Status definitions

- **PARTIAL** — concrete implementation exists, but at least one required behavior or end-to-end proof is missing.
- **UNVERIFIED** — code or tests exist, but current-main execution evidence is not yet available.
- **MISSING** — no adequate implementation was found during this audit.
- **BLOCKED** — requires a real dependency, policy, source, or authorization decision that is not available.

No group is marked fully complete in this ledger.

## Capability groups A–R

### A. Evidence identity and provenance — PARTIAL
- [x] Existing evidence identity, uploader identity, source type, original source text, upload time, source spans, and care-record ownership fields exist in the backend model.
- [x] Distinguish uploader from author/reporter/collector in the data model: `original_author`, `reported_by_label`, `reported_by_caregiver_id`, `collected_by_caregiver_id`.
- [x] Add collection time, event time, original timezone, origin channel, source reference, parent evidence link, ingestion time, and SHA-256 of the exact stored UTF-8 source text.
- [x] Bind evidence creation to the server-issued bearer session and reject cross-record identities.
- [ ] Verify the new migration and create/retrieve/hash behavior on current `main` in CI.
- [ ] Complete provenance for device identity, import/transfer lineage, original binary/document hash, and explicit evidence links to every event, claim, observation, decision, outcome, and review type.

### B. Evidence and knowledge classification — PARTIAL
- [x] Existing source type, event/assertion kind, evidence status, and epistemic-state structures distinguish several source and knowledge types.
- [ ] Establish one versioned canonical evidence-kind vocabulary covering direct observation, caregiver report, professional observation, person's own report, documented record, instrument measurement, photo/video, audio/transcript, imported information, historical recollection, derived observation, inference, hypothesis, missing, disputed, and superseded.
- [ ] Verify every ingestion and presentation path preserves these distinctions without collapsing them into a generic verified label.

### C. Source reliability and evidence quality — PARTIAL
- [x] Persisted evidence review records retain review type, outcome, rationale, criteria, policy version, source-attribution snapshot, reviewer identity, and timestamp.
- [x] Review history is append-only in the ORM and has a PostgreSQL migration trigger.
- [x] Unsupported numeric confidence is rejected in uncertainty assessments; frontend provenance no longer maps extraction scores through the arbitrary 0.85/0.65 thresholds.
- [ ] Add evidence-backed authenticity/tampering checks, extraction-quality evidence, measurement method/units/calibration, and reviewer-authority policy.
- [ ] No confidence calibration is available until a task-specific method has validation evidence.

### D. Claim-level evidence grounding — PARTIAL
- [x] Existing care facts, care assertions, evidence links, source spans, and persisted assertion statuses provide claim-related primitives.
- [x] Evidence integrity endpoint returns linked evidence and duplicate-content candidates.
- [ ] Implement one canonical versioned claim assessment lifecycle with the required statuses: supported, partially supported, contradicted, unverified, uncertain, conflicting, missing evidence, stale, superseded, and withdrawn.
- [ ] Add first-class support/contradiction/neutral relationships, exact source passages, claim dependency graph, correction/withdrawal audit, and re-review triggers.

### E. Corroboration and source independence — PARTIAL
- [x] Evidence integrity endpoint detects identical normalized source text and explicitly warns against counting duplicates as independent corroboration.
- [x] Evidence review captures an explicit independence-review criterion and preserves the reviewer/policy record.
- [ ] Persist source-origin and derivative lineage, copied/forwarded report relationships, source independence assessment history, and claim/event/observation-level corroboration.
- [ ] Distinguish no corroboration from attempted-but-failed corroboration in a canonical state model.

### F. Conflict detection, comparison, and resolution — PARTIAL
- [x] Persisted contradictions include context, event, effective interval, matching candidate IDs, evidence IDs, and resolution status.
- [x] Conflict resolution/reopening transitions have append-only audit records.
- [x] Matching considers context and explicit time overlap; invalid intervals and creation of already-resolved conflicts are rejected.
- [ ] Complete attribute/value/unit comparison, partial contradiction scope, evidence side-by-side views, authority matrix, partial resolution remainder, recurrence, and reopening across all related workflows.
- [ ] No automatic resolution by timestamp or majority vote.

### G. Structured observation extraction — PARTIAL
- [x] Existing ingestion/extraction and care-assertion paths preserve source text, evidence IDs, source spans, and separate event/recording times in supported workflows.
- [ ] Implement a universal typed extraction contract for negation, hedging, ambiguity, temporal expressions, pronoun/entity safeguards, extraction model/version, and structured validation.
- [ ] Verify document, audio, transcript, image, and text paths preserve originals and report unsupported formats/failures.

### H. Fact, inference, interpretation, and reported-versus-observed separation — PARTIAL
- [x] Uncertainty analysis distinguishes unknown, conflicting, ambiguous, partially known, insufficient, and uncalibrated states in supported tasks.
- [x] Unsupported assessment tasks now fail closed to unknown.
- [ ] Verify end-to-end separation of report vs direct observation, behavior vs interpretation, hypothesis vs finding, prediction vs outcome, correlation vs causation, and question vs assertion.
- [ ] Add dependency-aware inference invalidation and grounded alternative explanations; no diagnosis or causal claim from narratives or temporal sequence alone.

### I. Temporal evidence and historical continuity — PARTIAL
- [x] Care events preserve occurrence time, recorded time, timezone, and time provenance; evidence now has separate occurred, collected, uploaded, and ingested time fields.
- [x] Conflict matching checks explicit intervals and rejects reversed intervals.
- [ ] Complete review/correction/effective interval dimensions, approximate time precision, late/backdated evidence policy, domain-specific freshness, temporal claim validity, and historical snapshot reconstruction.

### J. Duplicate detection, entity resolution, and identity integrity — PARTIAL
- [x] Existing evidence integrity logic identifies exact normalized-text duplicate candidates without deleting records.
- [x] Evidence source text now has a deterministic SHA-256 fingerprint field for exact stored text.
- [ ] Add near-duplicate/document-version/semantic candidates, idempotent ingestion keys, event identity resolution, authorized person/entity merge proposals, and false-match review.
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
- [ ] Generate questions only from a traceable evidence gap, track intended answerer/priority/necessity/history, preserve answer provenance, and avoid repeating questions already answered by sufficient current evidence.
- [ ] An answer must not automatically resolve the underlying claim without assessment.

### O. Integrity, security, and governance — PARTIAL
- [x] Evidence creation now requires a server-issued bearer session matching person and uploader caregiver.
- [x] Reporter/collector caregiver references and parent evidence are validated against the same care record.
- [x] Evidence reviews and conflict-resolution history are append-only in ORM; evidence review has PostgreSQL immutability enforcement.
- [ ] Audit every evidence/claim/conflict/gap/question endpoint for scope authorization, every foreign ID, transactional rollback, idempotency, secure media handling, retention/deletion, backup/restore, and production failure behavior.
- [ ] Verify immutable source handling against all writers, direct SQL, and deployed PostgreSQL behavior.

### P. Grounded AI reasoning and explanations — PARTIAL
- [x] Existing uncertainty and trust-provenance layers represent retrieval scope, missing evidence, limitations, and non-calibrated confidence in supported flows.
- [ ] Validate every factual output claim against persisted claim/evidence links, source passages, freshness, conflicts, and source independence.
- [ ] Ensure citation integrity, no fabricated quotations, dependency-aware invalidation, and safe abstention across companion, summaries, appointments, LCR, and handoffs.

### Q. Frontend evidence workflows — PARTIAL
- [x] The persisted EvidenceReviewPanel is now connected to the current-care understanding component.
- [x] Current-care UI already exposes evidence-integrity inspection, duplicate candidates, interpretation limits, and review coverage.
- [x] Frontend verification asserts the LCR connection and guards against reintroducing arbitrary confidence thresholds.
- [ ] Complete source/claim detail navigation, exact passage display, provenance identities/times, conflicts and partial resolution, correction/retraction history, clarification workflow, and accessible error/retry/rollback states across all surfaces.

### R. Testing, verification, and operations — UNVERIFIED
- [x] Existing backend tests cover evidence-review persistence, cross-record denial, append-only history, contradiction scope, and uncertainty behavior.
- [x] PostgreSQL migration workflow validates a fresh schema, migration head, core table/column contracts, and several identity/persistence workflows.
- [x] Added tests for evidence provenance fields, source-text hashing, and evidence-create authorization.
- [ ] Current-main test and PostgreSQL migration runs must pass before these additions are considered verified.
- [ ] Add broader E2E/property/concurrency/failure tests for every state transition, ingestion retry, cross-person access, extraction path, claim dependency, source lineage, historical reconstruction, and production telemetry.

## Current implementation commits

### Frontend
- [LCR evidence review integration](https://github.com/getmycareos-star/SolenOS-landing/commit/499232feaa22adfbe77fbcd292d44b8492235844)
- [Nullable provenance confidence](https://github.com/getmycareos-star/SolenOS-landing/commit/e3f54c895adcbce13e3d53fa84439d5215d82c4f)
- [Remove arbitrary confidence thresholds](https://github.com/getmycareos-star/SolenOS-landing/commit/b95dfe82045b7c8b547b06230d54c9d976f635f4)
- [Add regression assertions](https://github.com/getmycareos-star/SolenOS-landing/commit/716d5ce4330a6234ebbec61459204a250d3efb15)

### Backend
- [Persist distinct provenance fields and source hash](https://github.com/getmycareos-star/SolenOS_backend/commit/d5e5aa40ca512c0fae152b892d625a6f53d5d896)
- [Expose provenance fields in schemas](https://github.com/getmycareos-star/SolenOS_backend/commit/eaa2f6d31b602bd8a8b7b5d8feb98a4872870a67)
- [Validate provenance references](https://github.com/getmycareos-star/SolenOS_backend/commit/f450b169466e94d9659e6561b6319f4baad8a957)
- [Add provenance migration](https://github.com/getmycareos-star/SolenOS_backend/commit/a5a2764141ba8a8a41020f3eae6bcaa14e2d5ddf)
- [Add migration and schema validation](https://github.com/getmycareos-star/SolenOS_backend/commit/4e65f66677caec65ed03ea377d4ebbfd635e3faa)
- [Require authenticated evidence creation](https://github.com/getmycareos-star/SolenOS_backend/commit/9e92221a7c08098eb98a883d5d77ca11446ad0cf)
- [Add provenance and authorization tests](https://github.com/getmycareos-star/SolenOS_backend/commit/95eb8bdff2143ed300cc9d1d7e89a5ad7e8bbd61)

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
