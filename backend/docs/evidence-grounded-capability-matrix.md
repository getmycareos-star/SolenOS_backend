# Evidence-grounded intelligence capability matrix

**Scope:** SolenOS backend and frontend `main` only. No pull request branch is part of this work.

**Status rule:** A code path or test file alone does not qualify a capability as complete. "Implemented, verification pending" means the implementation is present but the exact current `main` commit has not yet produced a completed passing workflow. "Partial" means a material part of the contract is not established end-to-end. This matrix must be updated as evidence changes; it is not a claim that all rows are complete.

## Current critical implementation slice

- Explicit evidence lineage is projected into uncertainty analysis from persisted parent evidence, original source references, and reviewed source relationships: `backend/app/services/evidence_lineage.py`, integrated by `backend/app/api/uncertainty.py`.
- Shared LCR retrieval now exposes evidence provenance fields and a bounded lineage summary to every workflow that uses `build_lcr_context`: `backend/app/services/lcr_context.py`. The lineage summary includes its bounded scope and explicitly states that lineage is not proof of truth.
- Unit tests cover parent evidence, normalized explicit source references, reviewed independence, shared-origin relationships, and scoped LCR context: `backend/tests/test_evidence_lineage.py`, `backend/tests/test_uncertainty_intelligence.py`, `backend/tests/test_lcr_context_schema_cache.py`.
- These changes have been committed directly to `main`. CI for the latest SHA is still in progress; do not treat the checks as passed until GitHub reports a completed result for that exact SHA.

## Capability groups A–R

| Group | Current classification | Existing implementation evidence | Material work still required before "complete" |
|---|---|---|---|
| A. Evidence identity and provenance | Partial; expanded in current slice | `backend/app/models/care.py`; migration `20261029_03_evidence_provenance.py`; evidence integrity endpoint in `backend/app/api/care_continuity.py`; shared projection in `backend/app/services/lcr_context.py` | Verify all ingestion/import/upload channels preserve author, reporter, collector, event time, collection time, original source and parent links; production persistence and source-document integrity remain distinct. |
| B. Evidence and knowledge classification | Partial | `Evidence.evidence_kind`, `Evidence.epistemic_status`; structured observation model and schema | Enforce a complete controlled vocabulary at every ingestion endpoint and verify reported/direct-observation distinctions end to end. |
| C. Source reliability and evidence quality | Partial | Persisted evidence review records and review API; policy-versioned criteria | No calibrated general confidence percentage; verify reviewer authority, criteria validation and immutable audit behavior across every review type. |
| D. Claim-level evidence grounding | Partial | `EvidenceClaimAssessment`, claim assessment API, claim dependencies, assessment impact and history | Semantic entailment is not automatically verified by reference linkage; test all ten statuses and all relationship types against live API/database behavior. |
| E. Corroboration and source independence | Partial; explicit lineage now shared | `EvidenceSourceRelationship`; near-duplicate candidates; `build_evidence_lineage`; uncertainty analysis; LCR context projection | Verify all workflow outputs consume and preserve lineage; copied/forwarded/shared-source and independent-source cases need API-level tests, including multi-hop and unselected parent nodes. |
| F. Conflict comparison and resolution | Partial | `Contradiction`, `ContradictionResolutionEvent`, conflict matching by scope/time, frontend `ConflictResolutionPanel` | Exercise create→partial resolve→reopen→resolve→history with PostgreSQL, concurrent writers, unauthorized actors, conflicting scope, and rollback failures. |
| G. Structured observation extraction | Partial | `EvidenceObservation`, source spans, epistemic status, hedge/negation/ambiguity fields and append-only corrections | Validate extraction quality against real supported document formats and adversarial ambiguous/negated/temporal passages; do not infer unsupported values. |
| H. Fact/inference/report separation | Partial | uncertainty analysis, epistemic fields, Companion unsupported-claim guards, frontend grounding utilities | The runtime guards are not a proof of semantic correctness. Add workflow-level evidence-ID/source-span grounding tests for Companion and generated summaries. |
| I. Temporal reasoning and history | Partial | occurred/reported/recorded/ingested/review timestamps; situation assessment history; temporal intelligence | Reconstruct historical snapshots from persisted versions; test timezone, approximate times, late-arriving reports, corrections versus new events, and overlapping/non-overlapping intervals in PostgreSQL. |
| J. Duplicate and entity resolution | Partial | idempotent evidence ingestion; exact/near-duplicate candidates; source lineage | Add ingestion retry and concurrency tests against the real unique constraint; semantic matches must remain candidates and must not auto-merge people or records. |
| K. Completeness and freshness | Partial | freshness review criteria, claim assessment impact, observation coverage, uncertainty gap policies | Verify domain-specific policy configuration, policy version history, and decision-specific sufficiency; no universal score is accepted without validation. |
| L. Longitudinal change detection | Partial | change engine and situation trajectory; evidence-linked change records and review history | Test matched windows, missing observations, changing reporting frequency, baseline selection, reversals and sparse-data limits against persisted API data. |
| M. Decision/outcome traceability | Partial | shared LCR context includes decisions and outcomes; decision/outcome workflow models | Verify each decision captures evidence available at decision time and each outcome links to actual completion/observation evidence; avoid causal claims from timing alone. |
| N. Clarification and evidence acquisition | Partial | open questions, answer provenance, append-only answer events, frontend clarification workflow | Verify question-to-claim linkage, authorized answer attribution, repeated question suppression, stale answers, contradictions and answer-triggered reassessment. |
| O. Integrity/security/governance | Partial | bearer-session scope, per-person filters, append-only records, source-text hash, migrations | Run adversarial cross-person tests and PostgreSQL concurrency, DB outage, rollback, backup/restore and migration tests on the latest exact SHA. |
| P. AI grounding and explanations | Partial | Companion context retrieval and unsupported-claim guards; uncertainty analysis; prompt serialization with explicit lineage limits | Validate citations, quotes, evidence IDs and claim scopes at runtime for Companion, summary, appointment and handoff responses; unsupported conclusions must fail closed. |
| Q. Frontend evidence workflows | Partial | `EvidenceReviewPanel`, `ClaimAssessmentPanel`, `ConflictResolutionPanel`, `ClarificationWorkflowPanel` connected through `src/components/mvp-workspace/LCRCurrentUnderstanding.tsx` | Exercise all workflows against real backend persistence and API errors; verify accessible source comparison, corrections, reopen history, retries and no false-success UI. |
| R. Verification and operations | In progress; not complete | backend tests, PostgreSQL migration workflow, frontend validation workflow, benchmark workflows | Wait for and inspect CI on the exact latest backend and frontend `main` SHAs; add missing end-to-end scenarios and resolve the known Netlify credential blocker before claiming production deployment. |

## Required release gates

1. **Shared-source corroboration:** exact duplicate text is only a duplicate candidate; explicit parent/source references and reviewed relationships define lineage. Similar wording is never proof of common origin or independence.
2. **Conflict lifecycle:** resolution and partial resolution require rationale and same-person evidence; each transition is appended to immutable history. Reopening must preserve previous resolution events.
3. **Temporal state:** event, observation, report, recording, ingestion and review times remain distinct. Historical reconstruction must use persisted versions, not current rows relabelled as history.
4. **Freshness/completeness:** a freshness or completeness result must cite its task/domain policy and assessment history; absent/unavailable is not a negative finding.
5. **Decision/outcome traceability:** decisions, completed actions, and observed/reported outcomes are distinct entities with explicit provenance.
6. **Grounded generation:** Companion, summary, appointment and handoff outputs must only assert what authorized retrieved records support; otherwise they disclose uncertainty or fail closed.
7. **Real verification:** current SHA, successful backend tests, PostgreSQL migration, container build, frontend validation/build and deployment evidence must be captured separately. Unit tests alone do not prove production end-to-end behavior.

## Known external blocker

At the latest observed frontend production workflow, the production build and validation passed but the Netlify deploy job failed at **"Fail clearly if Netlify credentials are not configured"**. This is a credential/configuration blocker, not proof of an application-code failure. It cannot be cleared by inventing or committing secrets; configure the required repository secrets in GitHub/Netlify, then rerun deployment.

