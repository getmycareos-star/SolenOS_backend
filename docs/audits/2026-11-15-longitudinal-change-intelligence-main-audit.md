# Longitudinal care-change intelligence — main-branch implementation record

Date: 2026-11-15  
Scope: `main` only in `getmycareos-star/SolenOS-landing` and `getmycareos-star/SolenOS_backend`.  
Purpose: connect persisted Delta comparisons to the primary Living Care Record and close a source-provenance defect. This is an implementation record, not a production sign-off.

## Main-branch execution map

1. **Daily Care ingestion**
   - The active workspace uses the Daily Care capture workflows.
   - The backend save path writes caregiver submissions as `CareEvent` records with linked `Evidence`.
   - The unified LCR projection reads those persisted sources through `GET /api/v1/care-continuity/current-care`.

2. **Longitudinal Delta reconciliation**
   - The frontend `CareDeltaPanel` posts a new report to `POST /api/v1/care-delta/reconcile` with `persist: true`.
   - The backend route derives person and caregiver identity from the server-issued session and rejects a mismatched person ID.
   - `reconcile_event` compares the incoming event with persisted assertions, prior events, evidence, open gaps, questions and contradictions.
   - A persisted `CareDeltaRun` stores the resulting JSON, including deterministic `computed_changes`, source references, claim provenance and engine metadata.
   - `GET /api/v1/care-delta/runs/{person_id}` returns saved comparisons and source-freshness status under the same session scope.

3. **Primary caregiver view**
   - `LCRCurrentUnderstanding` remains the primary workspace LCR.
   - It now mounts `CareChangeHistoryPanel`, which reads saved Delta runs from the backend.
   - The caregiver view separates source-supported changes from findings that still need checking, preserves differing reports, shows the source wording and dates, and warns when a source changed after a comparison.

## Defect fixed

Before this change, `computed_changes` was assembled before claim-specific source references were enriched. The detailed domain findings could have verified `source_record_refs`, while the simplified `computed_changes` projection persisted for the caregiver-facing view retained an empty reference list.

That mismatch meant the simplified projection could not safely expose the source-supported change even when the underlying finding had a matched source.

The Delta service now synchronizes each computed change with exactly one matching domain finding after source verification. It copies the verified references and support metadata. If the finding cannot be mapped unambiguously, or no source reference exists, the computed change is downgraded to uncertain and cannot be promoted as supported.

A regression assertion now requires every supported computed change to carry matched source references.

## Changes committed directly to main

### Frontend — `getmycareos-star/SolenOS-landing`

- Added `src/components/mvp-workspace/CareChangeHistoryPanel.tsx`.
- Mounted the panel in `src/components/mvp-workspace/LCRCurrentUnderstanding.tsx`.
- Extended `scripts/verify-change-intelligence-ui.mts` to guard backend retrieval, evidence requirements, uncertainty/conflict display, stale-source disclosure and primary-LCR mounting.
- The panel waits for a scoped person ID and uses the session-aware API client. It does not generate or infer changes locally.
- Saved comparisons are paginated (20 at a time); older records remain retrievable instead of silently disappearing after the first page.

### Backend — `getmycareos-star/SolenOS_backend`

- Updated `backend/app/services/delta_engine.py` to keep `computed_changes` source references aligned with verified domain findings.
- Updated `backend/tests/test_longitudinal_messy_care_benchmark.py` to assert that supported computed changes include matched source references.
- Corrected this document's benchmark-trigger description: `.github/workflows/situation-understanding-benchmark.yml` is manually dispatched, not an automatic push-to-main check.

## Existing architecture deliberately retained

- `CareEvent` and `Evidence` remain the original source records.
- `CareAssertion`, `EvidenceObservation`, contradiction and uncertainty records remain separate, source-attributed structures.
- The frontend Progressive Understanding Engine remains a separate in-session situation evolution path. It is not being declared the durable authority for cross-session history.
- The browser-side Care State Change Detector deliberately returns uncertainty when it has only narrative text and lacks comparable structured values plus verified observation coverage. That safety behavior must not be weakened into keyword-based worsening/improvement claims.
- The backend Delta Engine remains the persisted comparison/reconciliation path; no parallel classifier was introduced.

## Outstanding acceptance work

These items remain open; the changes above do not close them:

1. Run frontend `npm test`, `npm run typecheck` and `npm run build` on the exact current `main` commit.
2. Run the backend focused Delta tests and the complete messy-care contract benchmark on the exact current `main` commit.
3. Run the PostgreSQL-backed lifecycle/restart benchmark and capture its artifact and per-case metrics.
4. Verify the live Netlify → Railway path with an authorized test identity and disposable synthetic care record.
5. Verify create → reconcile → retrieve → fresh session/restart → retrieve → LCR rendering in the deployed environment.
6. Measure classification precision and recall, false positives and false negatives against independently authored expected labels. A green synthetic suite alone is not a 9/10 result.
7. Complete the remaining unification work so the Progressive Understanding path, structured evidence observations, backend Delta assessments and LCR explanation share a consistent persisted comparison contract. Do not merge their separate data models by assumption.

## Reliability statement

No frontend build, backend test run, PostgreSQL benchmark, live deployment check or production caregiver workflow is claimed as passing in this record. The current change is committed to `main`, but its runtime behavior still requires those checks.

A 9/10 rating remains unearned until the end-to-end evidence above is captured.

## Main commits from this work session

- Frontend main: `a4badc8264487feda290263d866e67c616be9228` (care-change history UI and source-contract guard).
- Backend main: `707f82326076169819834b186d9a2394d78c436c` (source-reference propagation, benchmark assertion, paginated API and route regression test).
- Architecture record: `1f127af1d49223ca6c0ff7b599fb2bdeaf81b58b`.

The repository status endpoint did not provide a successful frontend test/build result for the frontend commit. The backend showed a primary deployment status success, but that is not a pytest or PostgreSQL benchmark result. No build or test pass is claimed.


## Additional implementation pass — 11:00 UTC

- **LCR retrieval route:** the authenticated care-continuity context endpoint now composes the unified retrieval layer, including caregiver-scoped prior Companion dialogue marked as conversation context only, and caregiver-timezone-aware explicit temporal windows.
- **Temporal completeness:** explicit date-window Companion requests now page through up to 20 source pages (50 records per source per page). If more records remain after the ceiling, the retrieval contract retains the outstanding offsets and marks the projection incomplete; the response guard must not describe unseen records as absent.
- **Temporal paging tests:** added tests for page merging, deterministic offsets, preservation of the separate Companion-context source, successful completion, and fail-closed behavior when the page ceiling is reached.
- **Delta freshness regression:** added an ORM-level test that creates and updates baselines, changes and situations, then checks that their revisions make a saved Delta run stale.
- **Revision audit concurrency:** tracked-row updates now acquire the per-record PostgreSQL advisory lock before reading the persisted before-state. The after-state is assembled from that committed snapshot plus the columns changed by the current ORM write, preventing a stale identity-map object from erasing another writer's committed fields in the revision history. A PostgreSQL regression assertion checks that concurrent revision snapshots form a serial before/after chain.
- **Caregiver-facing Delta safety:** the history panel promotes supported changes only when freshness is explicitly current. Stale, missing, and unchecked freshness remain review-only; a missing freshness result is no longer treated as current.

These are source changes and regression tests, not test results. The PostgreSQL-specific concurrency behavior and the full authenticated route tests still need execution against the configured test database. Production must run the exact current backend commit before these changes can affect caregivers.
