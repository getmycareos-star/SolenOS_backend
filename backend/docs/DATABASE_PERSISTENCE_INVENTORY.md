# SolenOS database persistence inventory

**Scope:** canonical `main` in `SolenOS_backend` and `SolenOS-landing`.

**Release rule:** a schema/model or an existing test is not production verification. A category is not marked verified until the relevant PostgreSQL-backed test passes and, where production access is required, the live deployment and database lifecycle are verified. No completion or 9/10 claim is made by this inventory.

## Status definitions

- **Already persisted and verified** — durable PostgreSQL implementation and current reproducible lifecycle evidence have passed.
- **Newly implemented and verified** — new implementation and its PostgreSQL lifecycle evidence have passed.
- **Implemented but not verified** — code and/or PostgreSQL acceptance tests exist; the current verification run has not passed or is unavailable.
- **Partially implemented** — some lifecycle is durable, but at least one required state, history, consumer, or isolation guarantee is missing.
- **Blocked** — intentionally disabled or an external dependency prevents safe activation/verification.
- **Not implemented** — no durable implementation exists for the discovered requirement.

## Persistent data inventory

| # | Data category | Authoritative persistence / integration | Status | Outstanding proof or gap |
|---:|---|---|---|---|
| 1 | Caregiver accounts and identity | `CaregiverAccount`, `Caregiver`, `CaregiverProfile`; account, login, recovery and profile routes | Implemented but not verified | Run PostgreSQL account lifecycle, credential rotation, recovery and session revocation tests on current main. |
| 2 | Anonymous caregiver sessions and lifecycle | `AnonymousSession`; server-issued bearer token hashes, refresh/revoke routes | Implemented but not verified | Verify create → refresh → revoke → new login against PostgreSQL and application restart. |
| 3 | Care recipients and caregiver relationships | `Person`, `CaregiverProfile`, `CaregiverContext`; `GET/PUT /api/v1/care-profile` | Implemented but not verified | New profile route persists recipient name, shared caregiver-reported condition context, caregiver-specific relationship versions and audit events. PostgreSQL test covers a new account session and invited caregiver; CI must pass. |
| 4 | Caregiver permissions, roles and access grants | `CaregiverAccessGrant`, roles, responsibilities and invitation/transfer models | Implemented but not verified | Current PostgreSQL access-scope, role, invitation, revocation and cross-person tests must pass. |
| 5 | Daily Care entries and structured observations | `CareEvent`, caregiver observations, daily capture services and `POST /api/v1/daily/log` | Implemented but not verified | Run PostgreSQL write/read/reconciliation and refresh/new-session tests. |
| 6 | Original care evidence and source attribution | `Evidence`, evidence observations, provenance links, upload metadata | Implemented but not verified | Verify exact original source retention, authorship, event-time provenance and source references after restart. |
| 7 | Living Care Record assertions and historical versions | Care assertions, record versions/snapshots, provenance and reconciliation records | Implemented but not verified | Run current PostgreSQL LCR history, snapshot and retrieval tests; verify no projection becomes a competing source of truth. |
| 8 | Corrections, contradictions and superseded information | Correction records, amendments, uncertainty/conflict models and append-only source evidence | Implemented but not verified | Verify correction creates a new version, retains the original evidence, and reconciles only the authorized target. |
| 9 | Care history and longitudinal changes | Care events, delta runs, record revisions and bounded LCR projections | Implemented but not verified | Run PostgreSQL multi-event, history ordering, partial-source and new-session retrieval tests. |
| 10 | Documents, metadata, uploads, extraction and review decisions | `Evidence`, `EvidenceUpload`, `CaptureAnalysis`, document lifecycle and governed review/proposal records | Implemented but not verified | PostgreSQL document lifecycle test, durable object retrieval, checksum/integrity, authorization and review-history tests must pass. |
| 11 | Appointments and preparation records | Appointment workspace, revisions, proposals and linked evidence | Implemented but not verified | Run PostgreSQL appointment create/read/update/revision and cross-caregiver isolation tests. |
| 12 | Appointment outcomes and unresolved follow-ups | Appointment outcomes, linked open loops, reminders and source references | Implemented but not verified | Verify outcome writes and linked follow-ups survive a new session and are not represented as externally booked/confirmed without evidence. |
| 13 | Care handoffs and handoff history | Handoff drafts, immutable revisions, source snapshots, delivery receipts and acknowledgement records | Implemented but not verified | Run PostgreSQL draft/revision/approval/delivery/acknowledgement and recipient-isolation tests. |
| 14 | Open loops and reminders | Open-loop records/events/occurrences, `CareReminder`, assignment history and delivery attempts | Implemented but not verified | Run PostgreSQL create/update/resolve/reopen, idempotency, duplicate delivery and retry/recovery tests. |
| 15 | Care decisions and recorded outcomes | Decision/outcome records, source events, idempotency keys and linked follow-ups | Implemented but not verified | Run PostgreSQL decision → outcome → follow-up lifecycle and concurrent duplicate submission tests. |
| 16 | Care-attention assessments and supporting evidence | Attention/priority projections consume persisted care events, reminders and open loops | Partially implemented | A durable, versioned assessment record with its exact supporting source set and review outcome is not established by the inspected model inventory. Do not treat a computed projection as a saved assessment. |
| 17 | Two-week reviews and review history | Background review workflow can use persisted care events and background-job state | Partially implemented | A dedicated durable review instance/version and its caregiver-reviewed outcome are not established. Verify whether review outputs are only transient job results before enabling this workflow as a historical record. |
| 18 | AI Companion conversations and messages | `ConversationSession`, `ConversationMessage`; conversation create/history routes | Implemented but not verified | Run PostgreSQL conversation create/read after new authorized session and application restart; verify cross-caregiver and cross-person denial. |
| 19 | Companion context and evidence references | Typed source-record references on conversation messages and LCR retrieval metadata | Implemented but not verified | Verify references survive message history retrieval and that every referenced record is re-authorized at retrieval time. |
| 20 | Notification preferences, delivery attempts and delivery state | `CareNotificationPreference`, notifications, push subscriptions, delivery attempts and receipts | Implemented but not verified | Run PostgreSQL preference persistence, read receipts, push subscription ownership, retries and delivery-failure reconciliation tests. |
| 21 | User settings | Care profile and notification settings are server-backed; display/accessibility preferences are intentionally device-local | Partially implemented | Verify every active account-level setting. Device-local visual preferences are not care facts; legacy language/data-training keys must not be represented as cross-device settings unless backed by a server contract. |
| 22 | Audit events and security-relevant actions | `AuditLogEvent`, account security events, append-only PostgreSQL protections | Implemented but not verified | Run append-only, redaction/erasure, actor attribution and security-operation audit tests on PostgreSQL. |
| 23 | Community posts, reactions, saved stories and peer experiences | `CommunityPost` and `CommunityReaction` exist in separate community tables; router and UI are intentionally disabled | Blocked | Community routes are not registered and both community feature flags are false. Post/reaction code is not an active user path. Saved-story bookmarks are browser-local only and have no durable model/API. Do not enable the feature until moderation, bookmark persistence, authorization and PostgreSQL lifecycle tests are complete. |
| 23a | Community saved-story bookmarks | `solenos_saved_posts` in browser localStorage only | Not implemented | No caregiver-scoped PostgreSQL bookmark model or API exists. This remains blocked with Community and must be implemented before the feature is re-enabled. |

## Additional discovered persistence requirements

| Requirement | Current handling | Status | Outstanding proof or gap |
|---|---|---|---|
| Document binary storage | Object-storage metadata and document lifecycle integration exist in the backend migration/model chain. | Implemented but not verified | Confirm the production object provider, durable key, checksum, retention, owner authorization, and download after restart. A local temporary path is not sufficient. |
| Background jobs and worker state | Qwen/background job and operational worker heartbeat models exist. | Implemented but not verified | Verify retry/idempotency, worker restart, lease expiry and terminal failure records against PostgreSQL. |
| Browser offline drafts | Local device storage is used for unsaved drafts/recovery only. | Implemented but not verified | Confirm UI never reports a draft as saved to the authoritative Care Record before server write/readback. |
| Display/accessibility preferences | Browser-local preference storage. | Already persisted and verified | Device-local by design; do not treat as authoritative care information or account-wide settings. |
| Workspace pin/archive visibility | Browser-local preference storage, explicitly scoped to this device. | Already persisted and verified | Device-local by design; it must not archive or delete server-side care records. |
| Production telemetry | Production memory fallback now fails closed without PostgreSQL; telemetry write failures are logged. | Implemented but not verified | Run the frontend regression and verify production PostgreSQL-backed telemetry configuration. Telemetry is non-authoritative and must never block or impersonate a care-record write. |
| Production ORM/PostgreSQL schema reconciliation | Migration `20261120_07_notification_delivery_reconcile` repairs the known missing single-column indexes, notification timestamp nullability, privacy-subject redaction nullability, and notification delivery-attempt constraints; notification IDs now use the existing UUID-width contract. | Implemented but not verified | Production logs from the previously deployed revision reported 31 ORM/schema differences. The current main migration chain contains the repair, including deterministic legacy attempt backfills; verify current-main PostgreSQL migration validation and zero schema differences before claiming production reconciliation. |

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
5. Browser E2E uses the PostgreSQL-backed backend and proves profile persistence across refresh and a new account session.
6. Production `/ready` reports the expected migration head and schema contract; a real authenticated production read/write/readback test is completed without exposing real caregiver data.
7. Backup restore is rehearsed and verified against PostgreSQL.
8. Every category marked Partially implemented, Blocked or Not implemented is closed or explicitly excluded from the active MVP surface.

**Current release decision:** NOT VERIFIED. Production database access and the latest main-branch CI results are not yet available as passing evidence. No 9/10 reliability score is asserted.
