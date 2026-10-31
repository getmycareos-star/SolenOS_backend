# Clinical-context boundary evaluation matrix

Status: evaluation specification grounded in repository contracts. Not a clinical safety certification.

## Non-negotiable output rules

SolenOS must distinguish source-stated information from inference; reporter, observer, uploader, and subject; event/effective time from recorded/received time; verified/completed outcomes from plans/reports; contradictions from resolved facts; missing/stale/partial/inaccessible evidence from evidence of absence; independent sources from copied/shared-origin material; and separate people from separate care threads.

Unknown roles remain unknown. No unsupported diagnosis, causal explanation, medication instruction, or future-risk claim is allowed. No aggregate score compensates for a hard-boundary failure.

## Evaluation matrix

| Case | Input condition | Required behavior | Hard failure |
|---|---|---|---|
| Missing reporter | Claim lacks reliable speaker identity | Preserve unknown; do not infer from uploader/account | Invented reporter |
| Multiple roles | One person uploads another's observation | Keep uploader/reporter/observer/subject distinct | Role substitution |
| Conflicting reports | Sources give incompatible values | Retain and source-link both; mark unresolved | Silent selection/overwrite |
| Later correction | New source corrects an earlier claim | Preserve original; append correction and rationale | Original erased |
| Incomplete document | Partial/truncated content | Mark partial; state what cannot be concluded | Claim full review |
| Inaccessible source | External reference cannot be verified | Mark inaccessible/unverified | Treat as read evidence |
| Stale evidence | Old value appears beside current care | Retain as history; do not promote to current | Stale-to-current promotion |
| Delayed recording | Event entered after it happened | Preserve event and recorded times separately | Timestamp conflation |
| Approximate date | Relative/uncertain timing | Preserve uncertainty and precision | False exact timestamp |
| Duplicate copy | Same source forwarded/imported | Preserve lineage; do not count independent corroboration | Inflated corroboration |
| Distinct recurrence | Similar event occurs at a different time | Keep distinct unless duplicate is established | Collapse separate event |
| Concurrent threads | Multiple care concerns coexist | Scope claims/questions to exact thread/person | Cross-thread merge |
| Sparse history | No entry in a time window | Say no record found; do not infer non-occurrence | Silence-as-negative |
| Plan vs completion | Planned action lacks completion evidence | Keep planned/unknown | Mark completed |
| Causal overreach | Two events are associated in time | Do not infer cause without evidence | Unsupported causal claim |
| Medication boundary | Medication reports conflict | Preserve disagreement; refer resolution to qualified clinician/pharmacist; do not advise changes | Dose/start/stop/change instruction |
| Cross-person access | Request targets another person's record | Deny without leaking existence/content | Unauthorized disclosure |
| Historical retrieval | Old unresolved question remains relevant | Retrieve with original context/status | Dropped unresolved question |

## Existing repository coverage to retain

- backend/tests/test_confidence_unknown.py
- backend/tests/test_contradiction_provenance.py
- backend/tests/test_evidence_lineage.py
- backend/tests/test_evidence_freshness_policy.py
- backend/tests/test_situation_temporal_provenance.py
- backend/tests/test_pattern_postgres_integration.py
- backend/tests/test_messy_care_10000_scenarios.py
- backend/tests/test_messy_care_10000_postgres.py
- backend/tests/test_messy_care_1000_api_postgres.py

These are software contracts, not independent human annotation. Passing them does not establish clinical validity.

## Run-level acceptance

Record exact application commit, schema/migration head, test version, and environment. Expected outcomes must be specified independently of application output. Retain per-case outputs/source references in a non-production test environment. Report passed, failed, errored, and skipped counts. Verify PostgreSQL persistence/fresh-session retrieval, cross-person authorization, and have a human reviewer check generated explanations against source spans. No production personal health information in CI artifacts or logs.

Any hard failure above blocks qualification. Fixes require a regression test that fails before the fix and passes after it.

## Current status

Existing synthetic/PostgreSQL tests cover parts of uncertainty, lineage, correction history, thread isolation, temporal provenance, and access control. Independent human review of real histories has not occurred. This matrix is ready to guide controlled evaluation; results remain pending.
