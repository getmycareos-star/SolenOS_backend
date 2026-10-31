# Evidence-Grounded Care Intelligence — Implementation Scope

## Status

This document defines the acceptance contract for a dedicated implementation. It is **scope and test criteria only**; it does not claim that any capability is implemented or verified. Keep this pull request isolated from freshness-migration and Open Loop recovery work.

## Objective

Make longitudinal care changes defensible, auditable, uncertainty-aware, and consistent across SolenOS. The system must preserve what was reported, what was known at a given time, what changed later, and why any interpretation or follow-up was produced.

## Required implementation areas

### 1. Provenance and evidence attribution
Every material fact, detected change, conflict, state revision, summary statement, and recommendation must retain a resolvable provenance chain:
- source evidence and stable evidence identifiers;
- source/author identity and authorization context;
- event time and ingestion/recorded time;
- extraction, normalization, aggregation, and inference transformations;
- confidence/uncertainty and the policy/rule/model version used;
- correction, supersession, and retraction links.

No material output may silently lose its source chain. Derived statements must distinguish direct evidence from inference.

### 2. Conflicting caregiver reports
Preserve incompatible claims as separate attributable assertions. Distinguish disagreement from independent corroboration and repeated copies of one source. Support partial resolution, unresolved dimensions, reopening after new evidence, and explicit resolution rationale. Never select a winning report merely because it arrived last or has higher count.

### 3. Late-arriving information and corrections
Re-evaluate affected current state and downstream change assessments when relevant late evidence, corrected observations, or retractions arrive. Preserve original event time, received time, correction time, prior value, corrected value, actor, and reason. Reprocessing must be idempotent and must not rewrite the historical knowledge boundary.

### 4. Historical care-state reconstruction
Support reconstruction at a requested effective time and a separate “what was known by the system then” knowledge-time boundary. Include evidence, conflicts, uncertainty, revisions, and later-learned information without leaking future knowledge into an earlier snapshot. Mark incomplete history explicitly; never present partial reconstruction as complete.

### 5. Meaningful change versus noise
Validate detection against reviewed reference cases and adversarial datasets. Measure false positives, missed changes, duplicate detections, detection delay, evidence sufficiency, and uncertainty calibration. Account for observation coverage, reporting frequency, source dependence, baseline quality, and missingness. Insufficient evidence must produce an explicit uncertain/unknown result, not a fabricated stable or changed state.

### 6. Cross-module propagation and grounding
Use a shared, versioned evidence/change representation across LCR, Companion, care summaries, appointments, and handoffs. Each module must retain source references and uncertainty; no module may strengthen a claim, erase a conflict, or convert an inference into fact. Community content remains separate from the authoritative Living Care Record unless an authorized, explicit evidence-capture flow is completed.

### 7. Grounded follow-up recommendations
Every generated question or next step must link to the detected change or unresolved issue, supporting evidence, uncertainty, and a defensible reason for asking. Suppress recommendations when evidence is insufficient or stale. Record whether the follow-up was answered, whether the answer changed the assessment, and what evidence supports closure. No unsupported clinical advice.

## Required adversarial verification

- Competing reports from different caregivers, same caregiver, copied/shared source, and delayed submissions.
- Partial conflict resolution, reopening, retraction, and new contradictory evidence.
- Late-arriving evidence that changes a present assessment while preserving what was knowable earlier.
- Corrections and duplicate ingestion under retries/concurrency.
- Historical snapshots across both effective-time and knowledge-time boundaries.
- Missing/uneven observation coverage, sparse baselines, source bias, and correlated reports.
- Cross-module contract tests proving that provenance and uncertainty survive every handoff.
- Recommendation tests proving every question/action has evidence, uncertainty, and rationale references.
- Authorization and tenant-isolation tests for all evidence, history, conflict, and derived-state access.
- PostgreSQL integration, migration upgrade/downgrade safety where applicable, database-failure behavior, concurrency, and end-to-end API tests.

## Acceptance gates

The PR is not complete until:
1. All seven areas have production implementation, not documentation-only changes.
2. Tests exercise real PostgreSQL behavior for persistence, transactions, concurrency, and temporal reconstruction; SQLite-only or mocked tests are insufficient for those claims.
3. Reviewed reference cases and adversarial evaluation results are checked in or reproducibly generated, with metric definitions and thresholds documented.
4. Full backend tests, migration validation, relevant API integration tests, and container smoke checks pass on the same exact final commit.
5. Frontend contract/E2E coverage verifies the user-visible LCR, Companion, summary, appointment, and handoff flows against the real API where those flows are changed.
6. Failures, warnings, incomplete history, and insufficient evidence are reported separately. No fabricated metrics or blanket “9/10” claims.
7. The PR description records exact commit SHA and links to passing workflow runs. No production deployment or merge is implied by this draft.

## Explicit exclusions

- Freshness migration/Open Loop concurrency recovery already tracked separately.
- Qwen/model-provider availability work.
- UI redesign unrelated to the evidence-grounding contract.
- Automatic clinical diagnosis, medication-change advice, or unsupported causal claims.
