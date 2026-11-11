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
