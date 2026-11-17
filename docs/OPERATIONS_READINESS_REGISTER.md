# SolenOS operational readiness register

**Last checked:** 2026-11-16  
**Scope:** Operational maturity controls for the SolenOS production service.  
**Purpose:** Distinguish source-code implementation, CI evidence, provider configuration and rehearsed operational capability.  
**Status:** PARTIAL — this register is not a compliance certification or a production-readiness attestation.

## Decision rule

A control is **implemented** only when the relevant code/configuration exists; **CI-verified** only when a named check passed on the exact commit; **production-configured** only when the live provider setting is inspected; and **operationally verified** only after a production-equivalent rehearsal with retained evidence. One status must never be substituted for another.

No secrets, production database URLs, caregiver information, incident tokens or provider credentials may be committed to this repository.

## Control register

| Control | Current evidence | Status | Required completion evidence | Owner |
| --- | --- | --- | --- | --- |
| PostgreSQL backup/restore | Weekly scheduled synthetic PostgreSQL backup/restore CI; latest run [#44093659967](https://github.com/getmycareos-star/SolenOS_backend/actions/runs/44093659967) passed on `5e03bd8`; isolated artifact [#14332927062](https://github.com/getmycareos-star/SolenOS_backend/actions/runs/44093659967#artifacts) reports 960,908-byte backup, 0.438s backup, 1.72s restore, 2.289s end-to-end and verifies identity, consent, evidence, care events, document bytes, corrections, LCR revision and cross-caregiver isolation | CI-verified (synthetic source and isolated restore only; Railway's public DPA states vendor-level daily backups, but project-specific backup configuration and provider restore remain unverified) | Obtain provider backup configuration and timestamp evidence; complete an isolated restore from an actual provider backup; measure production RPO/RTO | Founder/operations |
| Production backup schedule | Railway production Postgres is a single-replica service with a 5 GB attached volume; current service configuration does not expose a verified backup schedule or PITR state | BLOCKED / unverified | Provider schedule, retention settings, last-success timestamp, alert route and backup access restrictions recorded in restricted operations register | Founder/operations |
| Production restore rehearsal | CI restores synthetic PostgreSQL into disposable CI database only | BLOCKED / unverified | Restore a provider backup into a separate production-equivalent database; verify schema, identity, care data, evidence and document bytes; record elapsed time and outcome; never overwrite production | Founder/operations |
| Incident response | Restricted operator API records a configured operator ID; PostgreSQL incident timeline rejects ordinary UPDATE/DELETE; audit events reject ordinary UPDATE/DELETE, allow only scoped erasure redaction and cannot be deleted. PostgreSQL trigger tests and privacy-request reviewer workflow tests passed on [backend test run #44092803870](https://github.com/getmycareos-star/SolenOS_backend/actions/runs/44092803870) | PARTIAL (database controls and tests pass; production operator token/ID configuration, verified contacts, tabletop rehearsal and independent evidence storage outstanding) | Verify primary and backup contacts; configure restricted operator secrets; run monitored incident intake; rehearse evidence export/hash; complete breach-notification tabletop | Incident Commander |
| Retention and deletion | Five-minute bounded expiry worker for encrypted privacy exports; metadata-only audit receipt; durable PostgreSQL success/failure heartbeat; `/ready` exposes worker state. PostgreSQL expiry test passed in [run #44092803870](https://github.com/getmycareos-star/SolenOS_backend/actions/runs/44092803870); production monitor verified a successful heartbeat 97.9 seconds old in [run #44093098978](https://github.com/getmycareos-star/SolenOS_backend/actions/runs/44093098978) | PARTIAL (temporary export expiry implemented, PostgreSQL-tested and production-observed; other data classes still lack an approved enforced schedule) | Approve per-data-class periods and legal basis; map deletion/expiry for all care data, derived data and logs; define backup expiry; independently verify eligible deletion | Founder + privacy counsel |
| Provider/subprocessor register | Netlify, Railway and Railway PostgreSQL are identified in source/deployment audit; AI runtime boundary and full provider chain unresolved | BLOCKED / incomplete | Complete provider list; service purpose/data categories; entity and contract/DPA; region and support access; subprocessors; retention; incident contact; transfer mechanism; evidence date | Founder + provider owners |
| Monitoring and alerting | Production monitor passed in [run #44093098978](https://github.com/getmycareos-star/SolenOS_backend/actions/runs/44093098978): deployed source `fd96f66` matches current main runtime; migration head `20261116_07_audit_events_append_only`; fresh worker heartbeat; both append-only triggers; core continuity OpenAPI, CORS and privacy-safe endpoint probes. Observation artifact [#14333600085](https://github.com/getmycareos-star/SolenOS_backend/actions/runs/44093098978#artifacts). GitHub issue alerts cover readiness and scheduled backup/recovery failures | PARTIAL (production smoke and internal alerting verified; external paging, continuous telemetry, and alert delivery to a human responder remain unverified) | Configure live telemetry and external alert destinations; monitor API/database health, 5xx, auth failures, queue failures and backup failures; verify alert acknowledgement and escalation | Engineering/operations |
| Audit integrity | PostgreSQL incident timeline and audit-event append-only/redaction trigger regressions passed in [run #44092803870](https://github.com/getmycareos-star/SolenOS_backend/actions/runs/44092803870); production `/ready` confirmed both triggers in [run #44093098978](https://github.com/getmycareos-star/SolenOS_backend/actions/runs/44093098978). Scoped privacy erasure can redact audit identifiers but cannot delete audit events. Hash chaining and independent evidence preservation are not established | PARTIAL | Independent export destination; restricted write/delete access; cryptographic digest verification; periodic integrity checks; evidence retrieval rehearsal; evidence access log | Engineering/operations |
| Breach applicability and notification | Procedure and draft notice exist | BLOCKED / unverified | Counsel-reviewed jurisdiction and role assessment; provider notification contacts; verified monitored recipient channels; deadline decision worksheet; notification delivery rehearsal using synthetic content | Founder + privacy counsel |

## Launch decision and scope restriction

Do not describe any BLOCKED or PARTIAL control as complete. Do not publish a statement that SolenOS is HIPAA compliant, NDPA compliant, breach-notification ready, has tested production disaster recovery, or has a complete subprocessor inventory based only on repository artifacts.

Until the retention schedule, deletion scope, provider processing boundary and incident contacts are verified, the safe launch posture is a **restricted early-access service**, not a general-purpose repository for real medical records. Do not encourage users to upload sensitive medical documents or rely on SolenOS as their sole care-history record. The existing privacy audit contains the underlying restrictions and must be reconciled before broader onboarding.

## Evidence record template

For each completed gate, record the following in a restricted operations system (not this public repository):

- Control ID and owner
- UTC timestamp and operator
- Exact source commit and deployed frontend/backend SHAs
- Provider/service and environment
- Test method and synthetic data scope
- Expected result and observed result
- Artifact reference and SHA-256 digest
- PASS / FAIL / BLOCKED, deviations and residual risk
- Remediation owner and due date

Never store the artifact itself if it contains production credentials or caregiver information in GitHub issues, CI logs or public artifacts.

## Required operating cadence

- **Each day during early access:** review production health, failed deployments, authentication anomalies, backup status and open incidents.
- **Weekly:** verify backup job success and alert delivery; review privileged access and unresolved security findings.
- **Monthly:** perform a restore rehearsal into an isolated target, verify evidence/document bytes and review provider changes.
- **Quarterly and after material changes:** review provider inventory, retention schedule, incident contacts, access permissions and notification procedure.

These are minimum proposed operating cadences, not evidence that the checks are already scheduled or performed. Assign named operators and create reminders in the restricted operations system before treating the cadence as active.
