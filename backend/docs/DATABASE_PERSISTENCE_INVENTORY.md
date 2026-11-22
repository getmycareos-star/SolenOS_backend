# SolenOS database persistence inventory

**Scope:** canonical `main` in `SolenOS_backend` and `SolenOS-landing`.

**Reviewed source snapshot:** backend `9a949d5e876dbd4063566fc91af2824d7ac879ff`; frontend `01a0282612deacb0f8747836f51c77c8cd288ad9`. Recheck these SHAs before using this inventory as release evidence.

**Release rule:** a schema/model or an existing test is not production verification. A category is not marked verified until the relevant PostgreSQL-backed test passes and, where production access is required, the live deployment and database lifecycle are verified. No completion or 9/10 claim is made by this inventory.

## Status definitions

- **Verified** — durable PostgreSQL implementation and relevant reproducible lifecycle evidence have passed.
- **Unverified** — implementation exists, but the relevant current-main PostgreSQL or end-to-end proof has not passed or is unavailable.
- **Partial** — some lifecycle is durable, but at least one required state, history, consumer, or isolation guarantee is missing.
- **Blocked** — intentionally disabled or an external dependency prevents safe activation or verification.
- **Missing** — no durable implementation exists for the discovered requirement.

## Persistent data inventory

| # | Data category | Authoritative persistence / integration | Status | Outstanding proof or gap |
|---:|---|---|---|---|
| 1 | Caregiver accounts and identity | `CaregiverAccount`, `Caregiver`, `CaregiverProfile`; account, login, recovery and profile routes | Unverified | PostgreSQL 17 E2E already verifies account creation, linking to the original caregiver/person, revocation of the anonymous bearer and account login after Uvicorn restart. The fresh-browser recovery-code redemption, credential rotation, account deletion and full account/profile lifecycle E2E must pass before this whole category is marked verified. |
| 2 | Anonymous caregiver sessions and lifecycle | `AnonymousSession`; server-issued bearer token hashes, refresh/revoke routes | Verified | PostgreSQL 17 workflow `44891918869` passed `postgres_anonymous_e2e.py`: anonymous session creation, bearer refresh/revocation, revoked-token denial, account-linked new-session login and same person/caregiver retrieval. Production deployed lifecycle and real device restart remain unverified. |
| 3 | Care recipients and caregiver relationships | `Person`, `CaregiverProfile`, `CaregiverContext`; `GET/PUT /api/v1/care-profile` | Verified | PostgreSQL job `45010206904`, step 12 passed: recipient profile/context and caregiver-specific relationship versions survived a new account login; invited caregiver access, independent relationship values, cross-person isolation, attribution and audit events were verified. |
| 4 | Caregiver permissions, roles and access grants | `CaregiverAccessGrant`, roles, responsibilities and invitation/transfer models | Unverified | Current PostgreSQL access-scope, role, invitation, revocation and cross-person tests must pass. |
| 5 | Daily Care entries and structured observations | `CareEvent`, caregiver observations, daily capture services and `POST /api/v1/daily/log` | Verified | PostgreSQL 17 workflow `44891918869` passed `test_unified_care_spine_capture.py` and `test_daily_care_review_postgres.py`, plus the anonymous-session HTTP lifecycle script. Production deployed read/write and cross-device UI journey remain unverified. |
| 6 | Original care evidence and source attribution | `Evidence`, evidence observations, provenance links, upload metadata | Verified | PostgreSQL 17 workflow `44891918869` passed source upload, source-linked observations, original wording and authenticated retrieval after Uvicorn restart. Production object-provider retrieval remains unverified. |
| 7 | Living Care Record assertions and historical versions | Care assertions, record versions/snapshots, provenance and reconciliation records | Verified | PostgreSQL 17 workflow `44891918869` passed explicit caregiver review → accepted LCR assertion, persisted source/evidence references, and authenticated current-care/history retrieval after Uvicorn restart. |
| 8 | Corrections, contradictions and superseded information | Correction records, amendments, uncertainty/conflict models and append-only source evidence | Verified | PostgreSQL 17 workflow `44891918869` passed caregiver correction with `supersedes_observation_id`, preserved original observation/evidence, rejected duplicate review transitions and re-read correction lineage after Uvicorn restart. |
| 9 | Care history and longitudinal changes | Care events, delta runs, record revisions and bounded LCR projections | Verified | PostgreSQL 17 workflow `44891918869` passed multi-event source/history retrieval, pattern/change reconciliation, account login after Uvicorn restart and same-record authenticated readback. |
| 10 | Documents, metadata, uploads, extraction and review decisions | `Evidence`, `EvidenceUpload`, `CaptureAnalysis`, document lifecycle and governed review/proposal records | Verified | PostgreSQL 17 workflow `44891918869` passed at main source `12a94c9`: upload/retrieval, document intelligence/review history, source-linked LCR retrieval, duplicate-source/idempotency and process-restart checks. The OCR contract deliberately keeps `client_declared_review_status` separate from `client_submitted_scan_review_status`; `review_attestation` remains `client_declared_not_server_verified`. Do not promote a browser-declared review to server-verified. Production Railway deployment, live S3 provider mode and production object read/write remain unverified. |
| 11 | Appointments and preparation records | Appointment workspace, revisions, proposals and linked evidence | Verified | PostgreSQL 17 workflow `44891918869` passed `test_appointment_workspace_postgres.py`; production deployed read/write and a separate-browser appointment revision journey remain unverified. |
| 12 | Appointment outcomes and unresolved follow-ups | Appointment workspace revisions, reviewed outcome proposals, accepted LCR assertions, and linked Open Loop follow-ups | Unverified | Current source includes `test_postgres_appointment_outcome_review_retrieves_only_accepted_assertions_in_fresh_session` in `backend/tests/test_appointment_workspace_postgres.py`. It exercises accept/reject, source-linked LCR reconciliation, Companion retrieval after bearer refresh, follow-up create/update/idempotent replay and caregiver-reported completion remaining open. The latest current-main PostgreSQL workflow has not passed yet; a separate-process restart/readback of this entire appointment outcome journey remains unproven. Do not represent caregiver-reported completion as verified closure. |
| 13 | Care handoffs and handoff history | Handoff drafts, immutable revisions, source snapshots, delivery receipts and acknowledgement records | Verified | PostgreSQL 17 workflow `44891918869` passed `test_handoff_postgres_integration.py`; production deployed handoff delivery and external recipient acknowledgement remain unverified. |
| 14 | Open loops and reminders | Open-loop records/events/occurrences, `CareReminder`, assignment history and delivery attempts | Verified | PostgreSQL workflow `44891918869` passed open-loop idempotent create, concurrent terminal-transition conflict handling, outcome recording/verification, reminder create/update/complete/cancel and authenticated retrieval after Uvicorn restart. Current PostgreSQL workflow `45010206904`, step 15, passed Web Push failure → retry → acceptance, append-only attempts, retry idempotency and lock-screen privacy. |
| 15 | Care decisions and recorded outcomes | Decision/outcome records, source events, idempotency keys and linked follow-ups | Verified | PostgreSQL 17 workflow `44891918869` passed caregiver-authored continuity decision persistence, open-loop outcome recording and explicit outcome verification with source references. |
| 16 | Care-attention assessments and supporting evidence | Deterministic assessment snapshots are stored in the existing `care_delta_runs` table with the source fingerprint, evidence IDs, creator caregiver and bounded source coverage | Unverified | A PostgreSQL test was added to `test_longitudinal_messy_care_benchmark.py` to prove snapshot creation, source-event/evidence linkage, retrieval through a new authorized session, history retrieval and cross-person denial. Current-main benchmark result is pending; process-restart retrieval remains to be proven. No competing assessment table is introduced. |
| 17 | Two-week reviews and review history | Existing `QwenBackgroundJob`, `QwenModelOutput` and `QwenJobEvent`; caregiver decisions are returned by job status and the two-week review history endpoint | Verified | PostgreSQL job `45010206904`, step 17 passed on the reviewed source snapshot: decisions, idempotent replay, new-login history retrieval, source references, cross-person isolation and rejection of in-place event updates. Alembic revision `20261122_01_qwen_job_event_update_guard` protects `qwen_job_events` from UPDATE while leaving DELETE available for privacy erasure through existing cascades. Live model-generation quality is a separate acceptance gate. No second review model is needed. |
| 18 | AI Companion conversations and messages | `ConversationSession`, `ConversationMessage`; authenticated `GET /conversations` discovery plus scoped `GET /conversation/{session_id}` history | Unverified | `backend/tests/test_companion_conversation_postgres.py` is wired into the main backend workflow and verifies list/read after a new account login, persisted source-record/evidence references, invited-caregiver denial, cross-person denial and fresh SQLAlchemy-session readback. Current-main CI must pass; a real separate-device/browser and process-restart journey remains to be verified. |
| 19 | Companion context and evidence references | Typed source-record references on conversation messages and LCR retrieval metadata | Unverified | Verify references survive message history retrieval and that every referenced record is re-authorized at retrieval time. |
| 20 | Notification preferences, delivery attempts and delivery state | `CareNotificationPreference`, notifications, push subscriptions, `CarePushDelivery`, append-only `NotificationDeliveryAttempt`, redacted `DeliveryFailureEvent` and delivery receipts | Verified | PostgreSQL workflow `45010206904`, steps 14–15 passed: preferences and read receipts across new account sessions, caregiver isolation, encrypted subscription ownership/revocation, provider failure → retry → acceptance, append-only attempt/failure history, worker replay idempotency and lock-screen payload privacy. Real production provider acceptance and production worker execution remain unverified. |
| 21 | User settings | Care profile and notification settings are server-backed; display/accessibility preferences are intentionally device-local | Partial | Verify every active account-level setting. Device-local visual preferences are not care facts; legacy language/data-training keys must not be represented as cross-device settings unless backed by a server contract. |
| 22 | Audit events and security-relevant actions | `AuditLogEvent`, account security events, append-only PostgreSQL protections | Verified | PostgreSQL workflow `45010206904`, steps 8–9 passed: security incident timeline append-only enforcement, audit event immutability, and reviewed erasure redaction. Profile update audit attribution also passed in step 12. |
| 23 | Community posts, reactions, saved stories and peer experiences | `CommunityPost` and `CommunityReaction` exist in separate community tables; router and UI are intentionally disabled | Blocked | Community routes are not registered and both community feature flags are false. Post/reaction code is not an active user path. Saved-story bookmarks are browser-local only and have no durable model/API. Do not enable the feature until moderation, bookmark persistence, authorization and PostgreSQL lifecycle tests are complete. |
| 23a | Community saved-story bookmarks | No active product workflow; Community remains disabled and fail-closed | Blocked | Intentionally excluded from the active MVP. Do not add bookmark persistence or re-enable Community as part of this programme. |

## Additional discovered persistence requirements

| Requirement | Current handling | Status | Outstanding proof or gap |
|---|---|---|---|
| Document binary storage | Object-storage metadata and document lifecycle integration exist in the backend migration/model chain. | Unverified | Confirm the production object provider, durable key, checksum, retention, owner authorization, and download after restart. A local temporary path is not sufficient. |
| Background jobs and worker state | Qwen/background job and operational worker heartbeat models exist. | Unverified | Verify retry/idempotency, worker restart, lease expiry and terminal failure records against PostgreSQL. |
| Browser offline drafts and legacy situation/timeline caches | Unsent text may remain device-local as a draft. Legacy situation/timeline localStorage caches are no longer hydrated, persisted, or exported as caregiver-visible care data; workspace projections now come from authenticated `GET /api/situation` with `cache: no-store`. The legacy Next.js `.data` filesystem adapter now fails closed for production read/write/delete/list/lock operations unless an explicit legacy opt-in is set; no production fallback is enabled. The unauthenticated legacy `/api/analyze` route is retired with HTTP 410 and no longer exposes that pipeline. | Unverified | Verify no active UI path reads old browser cache keys and that offline drafts are clearly distinguished from server-saved care records. Old cache bytes are intentionally not deleted automatically to avoid destroying recoverable legacy notes. Keep `SOLENOS_LEGACY_FS_STORE_ENABLED` unset in production. |
| Display/accessibility preferences | Browser-local preference storage. | Verified | Device-local by design; do not treat as authoritative care information or account-wide settings. |
| Workspace pin/archive visibility | Browser-local preference storage, explicitly scoped to this device. | Verified | Device-local by design; it must not archive or delete server-side care records. |
| Production telemetry | Production memory fallback now fails closed without PostgreSQL; telemetry write failures are logged. | Unverified | Run the frontend regression and verify production PostgreSQL-backed telemetry configuration. Telemetry is non-authoritative and must never block or impersonate a care-record write. |
| Caregiver feedback and consent-gated improvement examples | `LearningEvent`, `ReasoningMemory`, `ImprovementTrainingExample` and `ConsentRecord`; `POST /api/v1/feedback` accepts validated JSON, derives identity from the bearer session, requires explicit saved consent, and commits all feedback artifacts atomically. | Verified | PostgreSQL workflow `45010206904`, step 13 passed: new-login retrieval, caregiver attribution, cross-person denial, PII redaction in staged samples and rollback after a synthetic staging failure. Feedback text is sent in the request body, not query strings. |
| Production ORM/PostgreSQL schema reconciliation | Migration `20261120_07_notification_delivery_reconcile` repairs the known missing single-column indexes, notification timestamp nullability, privacy-subject redaction nullability, and notification delivery-attempt constraints; notification IDs now use the existing UUID-width contract. | Unverified | Production logs from the previously deployed revision reported 31 ORM/schema differences. Railway still runs deployment `c1396944-77a1-4d28-82f0-32d3c928454d` created 2026-11-20 from commit `474499444aee362de818738367abe667180d96f8`; the staged patch that removes the pinned commit SHA is not applied. Current-main PostgreSQL migration validation and live zero-drift verification remain outstanding. Do not claim production reconciliation. |

## Newly implemented profile persistence on current main

No new table was added. The implementation reuses the authoritative `persons`, `caregiver_profiles` and `caregiver_contexts` models:

- `Person.name` is the current care-recipient name.
- `CaregiverProfile.relationship` is the relationship for the authenticated caregiver, not a shared relationship field.
- `CaregiverContext` rows with `caregiver_id IS NULL` version the shared recipient name/condition context.
- Each new context version separately stores `reported_by_caregiver_id` from the authenticated server session; the caregiver-profile owner and the actor who reported the change are not conflated.
- Migration `20261120_10_caregiver_context_model_alignment` adds the active ORM's missing context columns, ownership foreign keys and person/caregiver/effective-time indexes without dropping legacy columns or rewriting existing rows. It refuses to enforce ownership/timestamp constraints if legacy rows have missing or orphaned person/caregiver attribution; those rows require explicit reconciliation, not fabricated ownership.
- `CaregiverContext` rows linked to a caregiver profile version that caregiver's relationship independently.
- Condition context is labelled `caregiver_reported`; it is not promoted to an independently verified clinical assertion.
- The update transaction uses row locks, closes the previous effective context, writes the new version and appends an `AuditLogEvent`.
- The API derives person/caregiver scope from the server-issued bearer session; caller-supplied ownership IDs are not accepted as authority.
- The frontend reads and writes `/api/v1/care-profile`. Legacy device-only profile values are migrated only after a successful server write. Share/export reads the care-recipient name from the same backend endpoint.
- Regression coverage: `backend/tests/test_caregiver_profile_session_scope.py`, `backend/tests/test_care_profile_postgres.py`, and `scripts/verify-care-profile-persistence.mts`.
- PostgreSQL CI step: `backend/.github/workflows/backend-tests.yml` runs `test_care_profile_postgres.py`.

## Verification gates still required

1. Current-main backend unit/integration tests pass.
2. Current-main PostgreSQL migration validation passes from a clean legacy schema and from the current migration head.
3. The PostgreSQL care-profile lifecycle test passes, including invited caregiver visibility and per-caregiver relationship separation.
4. Frontend typecheck, regression suite and production build pass.
5. Browser E2E uses the PostgreSQL-backed backend and proves profile persistence across refresh/new account session, server-only situation hydration, and Companion conversation restoration when the device-local pointer is absent.
6. PostgreSQL integration proves Companion conversation listing after bearer refresh and denies access across caregiver/person scopes; `test_two_week_review_postgres.py` proves two-week review decisions, idempotent replay, new-login history retrieval, source-reference preservation and cross-person isolation.
7. Migration tests prove legacy notification attempt backfills, caregiver-context ORM alignment, and zero schema-contract differences after upgrade.
8. Production `/ready` reports the expected migration head and schema contract; a real authenticated production read/write/readback test is completed without exposing real caregiver data.
9. Backup restore is rehearsed and verified against PostgreSQL.
10. Every category marked Partial, Blocked or Missing is closed or explicitly excluded from the active MVP surface.

**Current release decision:** NOT VERIFIED. Production database access and the latest main-branch CI results are not yet available as passing evidence. No 9/10 reliability score is asserted.

## Scanned-image review-status investigation (2026-11-22)

**Finding: no OCR review-status persistence defect is established by the inspected path.** The apparent status mismatch is an intentional trust boundary, not a claim that OCR or a human review has completed:

- The current frontend scan uploader (`SolenOS-landing/src/components/mvp-workspace/capture/extract-attached.ts`) submits `scan_review_status=not_reviewed` when saving an original scan. It does not claim that extracted text has been checked.
- The backend upload route in `backend/app/api/collaboration.py` validates the submitted value, stores it separately as `client_submitted_scan_review_status`, sets `client_declared_review_status` to `not_provided`, and records `review_attestation=client_declared_not_server_verified`.
- OCR extraction is performed later from the persisted original binary by `backend/app/api/document_lifecycle.py`; the original bytes and checksum remain authoritative.
- Actual document-detail decisions use persisted review events in `DocumentReviewEvent` through `backend/app/api/document_intelligence.py`. A browser-supplied scan flag is not a substitute for that review event.
- Regression coverage exists in `backend/tests/test_document_lifecycle.py::test_scanned_image_extraction_preserves_batch_page_number` and `backend/tests/test_frontend_contract_routes.py`; the former asserts the submitted value, server-side non-attestation and original binary preservation.

**Decision:** retain this fail-closed distinction. Do not change the backend to accept a browser-declared `caregiver_reviewed` value as proof of review. The remaining verification is to run these tests on a stable current-main commit and verify the browser journey displays the original as saved, OCR as pending/available/partial, and review as pending until a persisted caregiver decision exists. This investigation is not a claim that the full document journey has passed current-main CI.


## Current-main revalidation delta — 2026-11-22

**Inspected source snapshot:** backend `9a5bf29455a9786ae0dbaed34bc2608811e8235a`; frontend `cb18bd81d1a7ec0dbdc9b3e05d6c25b81300728c`. This is a source snapshot, not a verified release candidate.

### Changes made directly on main in this execution

- Backend `576573402a0dc0ad45f3e5f4fef86c7334e98ed8`: completed document-intake jobs without a matching saved document extraction are now exposed as failed/retryable instead of disappearing from the caregiver's processing list.
- Backend `456c6e6fdf3f4f68b8035a65e8e224daef2b7822` and `017479f3cfb2dd9bb07830fd62d51f3567131de8`: completed-job status is matched against saved analyses by the exact evidence IDs in the returned jobs, independently of the visible analysis page limit; ownership checks for the visible analysis list are batched rather than issuing one query per row.
- Backend `871e8d52eefa615135fca7b9f715df393061757d` and `5d485aeb88bede723f4a966b970eb46394fab69f`: PostgreSQL regression coverage for a completed job with no saved extraction and for a completed analysis outside the visible page limit.
- Backend `8f1b982fafe1e6f6585c90dfe625ccaee6e231d7`: added a PostgreSQL Daily Care → accepted LCR correction → new-session Care Assistant retrieval assertion. The test checks that the Assistant returns the corrected value without model inference.
- Backend `a522f0550e5521a164bceed118ac3d31d0b17888` and `5782e4ccc69c5670c8b8ed19ca2d5914141f6b9a`: corrected Companion history recall so a caregiver-approved assertion's current value takes precedence over the original source text. The preserved source text remains available for provenance and topic matching; it must not override the current reviewed value in a current-state answer.
- Backend `2f56e6a4a33f3ddaf48f9f034713a7e4fb7314c3` and `9a5bf29455a9786ae0dbaed34bc2608811e8235a`: cancelled document-intake jobs without saved analysis are now exposed as retryable failures, and deleted documents are excluded from the processing list. PostgreSQL regression coverage now checks both completed-without-analysis and cancelled-without-analysis states.
- Frontend `cb18bd81d1a7ec0dbdc9b3e05d6c25b81300728c`: repaired a malformed literal `\\n` in the caregiver UI language scanner that had swallowed the excluded-file declaration into a line comment. The scanner now has an actual newline before `const excludedFiles`.

### Current verification state

The following workflows were triggered for backend `9a5bf29455a9786ae0dbaed34bc2608811e8235a` and were pending/queued at last inspection:

- Backend tests — main validation: `45015809416`
- Backend validation: `45015809407`
- PostgreSQL migration validation: `45015809408`
- Document lifecycle PostgreSQL integration: `45015809430`
- Backup and recovery verification: `45015809440`

Frontend workflows for `cb18bd81d1a7ec0dbdc9b3e05d6c25b81300728c` remained queued:

- Frontend checks: `45015183298`
- Frontend main validation: `45015183196`
- Frontend validation: `45015183268`

No pending, queued, cancelled, or skipped workflow is counted as passing. The new PostgreSQL tests and Companion retrieval correction are **Implemented—not verified** until the current-main workflows complete successfully. The frontend scanner syntax correction is also **Implemented—not verified** until frontend validation completes.

### Read-only Railway production inspection (2026-11-22)

- Railway project: `SolenOS`; production environment: `19bc2a98-71e5-467b-abdd-7d9eb7011c35`.
- Backend service `SolenOS_backend` is online with 1/1 replicas and no active Railway warnings or critical alerts. Its latest live deployment is `c1396944-77a1-4d28-82f0-32d3c928454d`, created 2026-11-20, from backend commit `474499444aee362de818738367abe667180d96f8`.
- The live service source is pinned to that exact commit. It is not following the current `main` tip.
- Railway reports a staged environment patch `4cb3a568-545a-4f84-8044-8ebfa6df699f` with four service-source changes. The staged diff removes the pinned commit SHA while retaining the `main` branch/repository. It is still **STAGED**, not applied.
- The PostgreSQL service is online. The `solenos-care-documents` object-storage bucket exists in Railway's environment inventory in region `sjc`; this does not prove application-level upload/download, object ownership, or retention.
- No deployment, staged-patch acceptance, rollback, or production configuration change was performed.
- Production deploy logs were inspected read-only. At 2026-11-22 11:00 UTC, the deployed process logged `ORM schema drift count=31`. The drift list includes JSONList/JSON type differences, missing secondary indexes across evidence/handoff/notification tables, and nullability/length differences. This confirms the deployed database does not meet the full current ORM contract.
- The deployed `4744994` readiness implementation explicitly logs ORM drift as a warning and returns `migration_verified_with_orm_drift`; it does not fail readiness for those differences. Current `main` instead returns HTTP 503 whenever `database_schema_differences` is non-empty. Therefore Railway's current `Online` state is not evidence of zero drift, and deploying the current strict readiness code before reconciling production would risk taking the service out of readiness.
- A direct external request to the production `/ready` URL could not be retrieved through the available web access. Railway's service health overview is not a substitute for a successful authenticated readiness/schema check.
- Current verified candidate: **none**. Backend source `9a5bf29455a9786ae0dbaed34bc2608811e8235a` is not yet CI-verified. Do not remove the pin or deploy until the candidate's full tests, migration validation, backup/recovery and production schema checks pass and David explicitly authorizes deployment.

### Reassessment notes

- The scanned-image review-status difference remains an intentional trust boundary: the backend preserves the client-submitted value separately and does not treat it as proof of caregiver review. No change was made to weaken that boundary.
- The prior inventory's historical Verified labels refer to their cited earlier workflow runs and source snapshot. They do not establish that this newer source snapshot has passed current-main CI.
- No Railway or Netlify deployment was performed. Production database reconciliation, production object-provider read/write, backup restoration against production data, and deployed-versus-candidate comparison remain separate release gates.
