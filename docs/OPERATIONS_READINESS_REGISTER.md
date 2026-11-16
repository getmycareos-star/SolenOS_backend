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
| PostgreSQL backup/restore code | `.github/workflows/backup-recovery.yml`, `backend/scripts/verify_backup_restore.py`, `backend/docs/BACKUP_RESTORE_VERIFICATION.md`; synthetic PostgreSQL restore passed on main runtime commit `278bf07015851add9cd936d851a124422959f058` in run [#43926488253](https://github.com/getmycareos-star/SolenOS_backend/actions/runs/43926488253), with sanitized recovery artifact | CI-verified (synthetic source and isolated restore only) | Green exact-SHA workflow artifact; provider backup configuration snapshot; successful isolated restore using provider backup; measured RPO/RTO | Founder/operations |
| Production backup schedule | Railway production Postgres is a single-replica service with a 5 GB attached volume; current service configuration does not expose a verified backup schedule or PITR state | BLOCKED / unverified | Provider schedule, retention settings, last-success timestamp, alert route and backup access restrictions recorded in restricted operations register | Founder/operations |
| Production restore rehearsal | CI restores synthetic PostgreSQL into disposable CI database only | BLOCKED / unverified | Restore a provider backup into a separate production-equivalent database; verify schema, identity, care data, evidence and document bytes; record elapsed time and outcome; never overwrite production | Founder/operations |
| Incident response | Restricted operator incident API and timeline; PostgreSQL migration now blocks UPDATE/DELETE on incident events; CI exercises the trigger. Runbook exists | PARTIAL (timeline mutation protection implemented; contacts, rehearsal and independent evidence storage outstanding) | Verified primary and backup contacts; monitored intake test; configured restricted operator secret; synthetic incident API persistence test; evidence export/hash rehearsal; tabletop with PASS/FAIL/BLOCKED evidence | Incident Commander |
| Retention and deletion | Added a five-minute bounded expiry worker for encrypted privacy export artifacts, with metadata-only audit receipts, bounded batches and idempotent deletion; SQLite unit tests and a PostgreSQL integration test are present | PARTIAL (worker implemented; final PostgreSQL integration rerun pending; other data classes BLOCKED) | Approved per-data-class periods and legal basis; mapped deletion/expiry behavior; tested deletion of eligible data and derived copies; backup-expiry treatment; independent verification | Founder + privacy counsel |
| Provider/subprocessor register | Netlify, Railway and Railway PostgreSQL are identified in source/deployment audit; AI runtime boundary and full provider chain unresolved | BLOCKED / incomplete | Complete provider list; service purpose/data categories; entity and contract/DPA; region and support access; subprocessors; retention; incident contact; transfer mechanism; evidence date | Founder + provider owners |
| Monitoring and alerting | Production monitor passed on commit `c02184a86dc2d4665ed562c8d3b4f68782fe35fc` in run [#43926690508](https://github.com/getmycareos-star/SolenOS_backend/actions/runs/43926690508): production source provenance, readiness, migrations, core OpenAPI routes, CORS and privacy-safe endpoint observations; artifact `solenos-production-observation-43926690508` retained 90 days. Failure alert created and closed incident issue #535 after recovery | PARTIAL (GitHub issue alert and production smoke evidence verified; external paging and continuous telemetry alert delivery unverified) | Live telemetry destinations; alert rules and recipients; test alert delivery; monitor API/database health, error rates, auth failures, queue failures and backup failures; documented retention/access; no sensitive payload logging | Engineering/operations |
| Audit integrity | PostgreSQL incident timeline rows reject ordinary UPDATE/DELETE; only the approved privacy-erasure transaction can delete them. General audit events remain redacted by the privacy workflow and are not independently tamper-evident | PARTIAL (final PostgreSQL regression pending) | Independent export destination; restricted write/delete access; digest verification; periodic integrity check; retrieval rehearsal; evidence access log | Engineering/operations |
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
