# SolenOS production privacy audit — 2026-11-11

## Purpose and evidence standard

This is an engineering evidence register, not legal advice, a compliance certification, or a statement that unverified controls exist. It records what was observable in the current `main` source and connected production deployment metadata on 2026-11-11.

Repository scope:
- Frontend: `getmycareos-star/SolenOS-landing`, main at `94d7220f12d6d0af91274d705e87a48d0986ad57`.
- Backend: `getmycareos-star/SolenOS_backend`, main at `fc34d6931f67e6bbeab6900d5b2247c664fd9267`.

Production deployment snapshots inspected:
- Netlify site `solenosai.netlify.app`; production deploy inspected on 2026-11-11. Netlify function region configured as `us-east-2`.
- Railway production backend service in `sfo`; Railway PostgreSQL service and persistent volume configured. The Railway backend service is manual-deploy-only.
- These facts establish configured service regions, not the complete residency of provider backups, support access, logs, subprocessors, or every network request.

## Verified data flows and stores

| Data or processing | Evidence in source/deployment | Status and limitation |
|---|---|---|
| Caregiver/person identity and session | Backend `persons`, `caregivers`, `anonymous_sessions`; session stores a token hash and expiry | Implemented. Confirm access controls through dedicated tests and production verification. |
| Structured care history and evidence | Backend SQLAlchemy care models and PostgreSQL deployment | Implemented. Exact table-level retention schedule not established. |
| Uploaded document bytes | `EvidenceUpload` model stores original bytes, filename, MIME type, size and SHA-256 | Verified in source. Document bytes are not exclusively an external object-storage concern. |
| Document extraction | Backend document lifecycle/extraction modules and capture analysis records | Verified in source. Exact processor boundary and retention of extracted text require runtime tracing. |
| AI companion | Backend Qwen gateway and Ollama configuration | Qwen/Ollama path exists. Confirm actual production endpoint, model, network boundary, prompt contents, logs and provider-side retention before publishing definitive claims. |
| Optional improvement consent | Frontend PolicySettingsPanel calls authenticated `/api/v1/consent`; backend ConsentRecord | Implemented as a separate optional preference. Confirm all submission paths enforce the same policy. |
| Account deletion | Backend `DELETE /api/v1/accounts/me` | Deletes the caregiver account and revokes linked sessions; response explicitly indicates care record is retained. This is not care-data erasure. |
| Browser-local data clearing | Frontend Privacy & Data settings | UI explains this does not delete server-held care history. |
| Access/export/correction/erasure requests | Migration creates request-related tables | Schema preparation alone does not establish authenticated request APIs, fulfilment workers, secure export delivery, or verified erasure. |
| Privacy incident handling | Migration creates privacy incident and event tables | Schema preparation alone does not establish triage, escalation, evidence handling, regulatory assessment or notification workflows. |
| Request telemetry | Backend request metrics middleware | Source states that request/response bodies, query strings, headers, care identifiers and exception messages are excluded. Validate all other logging paths independently. |

## Consent and public-document alignment

The frontend consent panel submits accepted terms version `2026-10`. Public legal content inspected in the frontend contains an earlier July 2026 revision marker. This version mismatch must be resolved before collecting new consent.

The optional improvement choice is distinct from required acknowledgement. The product must not describe optional improvement consent as consent to train models or broadly reuse care records unless the actual processing purpose, data scope, recipients and controls support that statement.

## Critical unknowns — do not convert these into policy promises yet

1. Complete list of subprocessors and service providers, including Netlify, Railway, database, AI runtime, email, monitoring, analytics, crash reporting, support and any extraction services.
2. Physical and logical locations for primary data, replicas, backups, logs, support access and AI processing.
3. Provider contracts and applicable data-processing terms, international transfer mechanism and onward-transfer controls.
4. Exact production AI route(s), payload minimization, prompt/output retention, model telemetry and any external model provider.
5. Retention periods for care records, source documents, extracted text, AI conversations, audit events, account/security records, support records and backups.
6. End-to-end erasure behavior, including linked evidence bytes, derived records, indexes, caches, replicas and backup expiry.
7. Export completeness, secure delivery, expiry and access logging.
8. Requester identity and authority checks when the caregiver is not the data subject or when multiple caregivers share a person's record.
9. Operational incident response, processor escalation, notification decision-making and tested notification templates.
10. Legal applicability assessment for FTC Health Breach Notification Rule, Nigeria NDPA/GAID, and any other relevant jurisdictions.

## Release blockers

Do not represent the following as available until implemented and tested:
- Complete care-record export.
- End-to-end erasure of eligible personal data.
- Defined and enforced retention schedule.
- Verified provider and cross-border transfer register.
- Operational privacy incident response and notification process.

Do not invite caregivers to upload real medical documents or rely on SolenOS as their sole care-history store until these blockers have been addressed and the production release has been verified.

## Required verification evidence

For each control, retain:
- Source commit and deployment SHA.
- API contract and authorization test.
- Database migration revision and schema evidence.
- Positive and negative integration tests using synthetic records.
- Production smoke-test result that does not expose real care data.
- Operational owner, failure path, retry behavior and completion evidence.

## Immediate implementation order

1. Reconcile production deployment SHAs and Alembic head before treating `main` as deployed.
2. Trace all data ingress, persistence, AI egress, logs, backups and third-party processors.
3. Implement and test data-subject request APIs and workflows with authority checks.
4. Implement retention and verifiable erasure, including document bytes and derived data.
5. Implement secure export.
6. Implement incident management and response runbook.
7. Synchronize consent versions and rewrite public documents only from verified facts.
8. Obtain qualified privacy counsel review before US/Nigeria real-user early access.

## Change control

No production secrets, care records, personal data, access tokens or provider credentials belong in this document. Revalidate all deployment facts immediately before publication because provider regions and service configurations can change.


## Remediation update — authenticated privacy request intake

Backend `main` now includes an authenticated request-intake API:
- `POST /api/v1/privacy/requests` accepts access, export, correction and deletion request types.
- `GET /api/v1/privacy/requests` returns only requests scoped to the authenticated caregiver and care record.
- Each submission records the authenticated caregiver and care-recipient scope, a claimed authority basis, timestamp, request correlation ID and audit event.
- New requests remain `received_pending_authority_review`. The stored metadata explicitly records `authority_verified=false` and `fulfilment_started=false`.

This is deliberately an intake mechanism only. It does **not** verify legal authority, perform an export, correct a record, delete care data, or promise a completion deadline. Account/session authentication is not proof that a caregiver has legal authority to exercise another adult's data rights. A separate controlled review and fulfilment workflow remains required.

The ORM mapping reuses the existing `data_subject_requests` table from migration `20261027_08_privacy_delivery`; no production migration or deployment was initiated for this intake change. CI must pass before this source change is treated as verified.


## Remediation update — designated initial privacy reviewer

Backend `main` now includes a restricted review decision workflow for the initial designated reviewer:
- Reviewer queue: `GET /api/v1/privacy/requests/reviewer/pending`.
- Review decision: `POST /api/v1/privacy/requests/{request_id}/review`.
- Both routes require the separate designated-reviewer credential in `X-SolenOS-Privacy-Reviewer-Token`; a caregiver bearer session alone cannot access reviewer operations. The reviewer credential is independent of caregiver sessions and must be kept server-side.
- Reviewer access is fail-closed unless both deployment secrets are configured: `SOLENOS_PRIVACY_REVIEWER_TOKEN` (unique random secret, at least 32 characters) and `SOLENOS_PRIVACY_REVIEWER_ID` (stable reviewer actor ID; initial value intended: `david_abuchi`).
- Review requires a written decision basis. Approval additionally requires a description of the authority evidence checked. Each decision is recorded in the request metadata and `audit_log_events` under the configured reviewer actor ID.
- A request can be reviewed only once while it is in `received_pending_authority_review`. Approval moves it to `approved_pending_fulfilment_assessment`; rejection moves it to `rejected`.

**Important boundary:** this is a recorded reviewer decision, not a legal determination engine. The evidence description is an accountable human attestation, not independent verification by the software. Approval does not itself export, correct, delete, or disclose data and does not bypass legal holds, retention requirements or scope checks. The primary reviewer credential remains a single configured identity for intake review, export and correction; those operations do not yet have multi-reviewer identity management.

**Deletion separation of duties:** the deletion fulfilment route now uses a distinct 'X-SolenOS-Privacy-Deletion-Reviewer-Token' credential and reviewer identity. It fails closed unless 'SOLENOS_PRIVACY_DELETION_REVIEWER_TOKEN' and 'SOLENOS_PRIVACY_DELETION_REVIEWER_ID' are configured, the deletion reviewer ID differs from the primary reviewer ID, and the deletion token differs from the primary reviewer token. Reusing the primary reviewer credential for deletion is rejected. This is a two-credential separation control, not a complete reviewer identity-management system; production provisioning, custody, rotation and independent human assignment still require verification.

The reviewer and deletion-reviewer headers are included in the backend CORS allowlist. No production secrets were created or changed, and no deployment was initiated.

### Verification evidence — 2026-11-11

The reviewer workflow tests have now passed against PostgreSQL in GitHub Actions on backend commit `dc80c4d7c2c666524821ff86d08b21544e879e4a`:
- PostgreSQL migration validation: run [43091931655](https://github.com/getmycareos-star/SolenOS_backend/actions/runs/43091931655) — passed, including the dedicated privacy request reviewer test step.
- Backup and recovery verification: run [43091931385](https://github.com/getmycareos-star/SolenOS_backend/actions/runs/43091931385) — passed, including restored identity, evidence, care-event/LCR source-context and document-byte retrieval through authenticated APIs.
- Document lifecycle PostgreSQL integration: run [43091931421](https://github.com/getmycareos-star/SolenOS_backend/actions/runs/43091931421) — passed.
- Backend validation: run [43091931531](https://github.com/getmycareos-star/SolenOS_backend/actions/runs/43091931531) — passed.
- Backend unit/integration test job and PostgreSQL migration job: run [43091931400](https://github.com/getmycareos-star/SolenOS_backend/actions/runs/43091931400) — both test jobs passed; the separate real-Qwen image/inference job was still running when this evidence entry was written.

These results verify the tested source commit and synthetic CI fixtures only. They do not establish production deployment, live-user privacy fulfilment, legal authority verification, or availability of export, correction, or erasure workflows.


## Remediation update — initial privacy fulfilment handlers (source only; CI pending)

Backend `main` now contains initial fulfilment handlers. These are implementation claims from source inspection only; do not treat them as verified production controls until the current commit's CI, migration and operational checks pass.

### Export

- Reviewer-only `POST /api/v1/privacy/requests/{request_id}/fulfil-export` generates a ZIP after the request has been approved.
- Caregiver-only `GET /api/v1/privacy/requests/{request_id}/export` downloads the archive through the authenticated bearer session. No public object URL is issued.
- Archive bytes are encrypted with Fernet before storage in the PostgreSQL `privacy_export_artifacts` table. The deployment must provide `SOLENOS_PRIVACY_EXPORT_ENCRYPTION_KEY`; the handler fails closed with HTTP 503 when it is missing or invalid.
- Archives expire after 24 hours. Expired archives are removed when privacy requests are listed, a new export is generated, or the expired archive is requested. This is request-driven cleanup, not a dedicated scheduled retention worker.
- The ZIP includes person-scoped ORM records, caregiver-attributed records, requester-owned conversation messages, and original document bytes uploaded by that caregiver. It excludes other caregivers' attributed rows, authentication/session secrets, and suspended Community content. Archive size is limited to 100 MiB.
- The exporter does not include provider backups, infrastructure logs, external processor data, or unregistered database tables. Completeness against every production data store remains unverified.

### Correction

- Correction intake can bind a request to an existing insight description or care-event description and snapshots the original value/version at submission.
- Reviewer-only `POST /api/v1/privacy/requests/{request_id}/fulfil-correction` rejects stale targets, creates an append-only `corrections` record, applies the reviewed description change in one database transaction, and records an audit event.
- Care-event changes pass through the existing care-state revision listener; the original source evidence remains immutable.
- This first implementation supports only insight-description and care-event-description corrections. It does not yet support amendments to every evidence, assertion, document extraction, account, or third-party data category.

### Deletion

- Reviewer-only `POST /api/v1/privacy/requests/{request_id}/fulfil-deletion` requires an independent second reviewer, separate from the reviewer who approved the request.
- The second reviewer must attest that backups containing the subject's data have expired or are otherwise irrecoverable and provide a written evidence reference. Only a hash of that evidence is retained in the fulfilment/audit receipt; the handler does not independently query the backup provider.
- Any active or unclassified legal hold blocks fulfilment.
- The current deletion handler is deliberately limited to a single-caregiver care record. It fails closed when multiple caregivers are linked rather than deleting their accounts or private contributions.
- For the supported scope, the handler discovers person/caregiver-scoped ORM rows and foreign-key dependants, removes registered data and original document bytes, removes generic migration-created person/caregiver rows, and redacts prior request/audit metadata while retaining a minimal receipt.
- This does not establish erasure from external processors, unregistered stores, infrastructure logs, caches, provider replicas, or any backup that the reviewer has not independently verified as expired/irrecoverable. Multi-caregiver erasure and a formal retention schedule remain unresolved.

### Required release conditions

1. Configure and rotate a unique 'SOLENOS_PRIVACY_EXPORT_ENCRYPTION_KEY' in the deployment secret store; do not place it in source control.
2. Provision independent, high-entropy 'SOLENOS_PRIVACY_REVIEWER_TOKEN' / 'SOLENOS_PRIVACY_REVIEWER_ID' and 'SOLENOS_PRIVACY_DELETION_REVIEWER_TOKEN' / 'SOLENOS_PRIVACY_DELETION_REVIEWER_ID' values in the deployment secret store. Keep the reviewer identities distinct; do not place credentials in source control.
3. Verify the current migration and PostgreSQL test workflows, including export ZIP inspection, cross-caregiver denial, correction provenance, deletion dependency removal, legal-hold rejection and independent-review enforcement.
4. Exercise export and correction with synthetic production-like data before real-user access.
5. Establish a documented backup-retention schedule and a restore procedure that cannot reintroduce erased records. Backup expiry evidence must be independently checked, not accepted as an unchecked assertion.
6. Trace all external processors, replicas, caches and unregistered stores before claiming complete erasure.
7. Expand correction coverage and define mixed-caregiver/third-party data handling before claiming universal correction or deletion fulfilment.

At the time this section was added, GitHub Actions runs for the new source commits were queued and had not produced test conclusions. No production deployment or production secret change was performed. These handlers are not a production-readiness certification.
