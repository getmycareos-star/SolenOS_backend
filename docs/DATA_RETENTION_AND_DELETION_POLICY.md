# SolenOS data retention and deletion control framework

**Last checked:** 2026-11-16  
**Status:** POLICY FRAMEWORK ONLY — no complete approved retention schedule is currently established or enforced.

This document deliberately does not invent retention periods. Retention durations must be approved against actual purposes, user expectations, legal obligations, provider capabilities, backup behavior and the needs of the care-record service. Until those decisions and technical controls are complete, SolenOS must not claim that a comprehensive retention policy is implemented.

## 1. Core rules

1. Collect and retain only data necessary for a documented purpose.
2. Assign a named owner, lawful basis/authority assessment, retention trigger and retention period to every data class.
3. Do not silently delete care history, evidence or documents as a side effect of account deletion.
4. Distinguish account closure, browser-local clearing, care-record deletion, document deletion, derived-data deletion and backup expiry.
5. A privacy request is not fulfilled merely because it was submitted, approved or queued.
6. Do not purge records automatically while the relevant retention period, legal hold, authority check or deletion semantics are unresolved.
7. Do not use indefinite retention as a substitute for making and enforcing a retention decision.
8. Preserve only the minimum audit evidence needed to demonstrate the request and its outcome; do not retain unnecessary copies of the deleted content.

## 2. Data-class schedule — approval required

| Data class | Examples | Retention period / trigger | Required deletion or expiry behavior | Current status |
| --- | --- | --- | --- | --- |
| Account and authentication | Caregiver profile, credentials, session hashes, device grants | NOT APPROVED | Revoke sessions immediately on account closure; define account-record deletion and security-log exceptions | PARTIAL |
| Care-recipient identity and relationship | Person records, caregiver links, authority evidence | NOT APPROVED | Define relationship closure, shared-caregiver handling and legal-authority review | BLOCKED |
| Structured care record | Events, observations, appointments, decisions, outcomes and LCR state | NOT APPROVED | Define user-requested deletion, correction, legal holds, linked-record handling and audit trail | BLOCKED |
| Evidence and source documents | Source text, uploaded files, binary document bytes, hashes and extraction candidates | NOT APPROVED | Delete eligible source bytes and derived content; verify no live references or accessible copies remain | BLOCKED |
| AI conversations and derived context | Conversation turns, retrieved context, embeddings/caches if any, model inputs/outputs | NOT APPROVED | Trace every persistence/cache/log path and provider retention; support deletion and provider request where applicable | BLOCKED |
| Audit, security and incident records | Access events, authorization decisions, security incidents and evidence digests | NOT APPROVED | Define proportionate security retention, restricted access and legal hold; do not erase incident evidence without review | PARTIAL |
| Export artifacts | Generated exports and temporary download artifacts | Expiry must be configured and verified | Enforce expiry, deny access after expiry and remove stored bytes | PARTIAL — export artifact expiry code exists; full delivery/storage verification remains required |
| Operational logs and backups | Application/provider logs, database backups, CI artifacts | NOT APPROVED | Configure per-provider log and backup retention; verify expiry and restore boundaries | BLOCKED |

No numeric duration in this table is intentionally supplied. The owner must approve a duration and its trigger for every row before the schedule can be called complete.

## 3. Privacy request lifecycle

Use the existing authenticated privacy-request workflow as intake, not as proof of fulfilment.

Required states and evidence:

- **Received:** request ID, timestamp, request type and authenticated requester scope recorded.
- **Authority review:** identity and authority over the affected person's information assessed; caregiver login alone is not proof of legal authority.
- **Decision:** approval/rejection and reason recorded by an authorized reviewer.
- **Fulfilment:** each affected data store, document byte, derived record, cache, provider and backup-expiry mechanism is accounted for.
- **Verification:** independent check confirms the approved scope was completed and unrelated caregivers' data was not affected.
- **Closure:** completion timestamp, resolution code, exceptions and minimum necessary audit evidence retained.

A request response must not say data has been deleted unless the corresponding deletion and verification have actually completed. Account deletion must not be described as care-record erasure. Browser-local clearing must not be described as server-side deletion.

## 4. Implemented bounded expiry control

Encrypted privacy export artifacts now have a dedicated application lifespan worker:

- Runs every five minutes and deletes at most 100 expired export rows per pass.
- Uses row-level locking with `SKIP LOCKED` where supported to avoid competing workers processing the same rows.
- Writes a metadata-only `privacy_export.expired` audit receipt with artifact identifier, byte count and SHA-256 digest; archive bytes are not copied into the audit event.
- Is idempotent and retries on the next pass after transient failures.
- Does not delete care records, source evidence, original documents, conversations, accounts, or audit history.
- Has focused tests for expired-versus-live artifacts, bounded batches, idempotency and invalid batch sizes.
- Persists a durable success/failure heartbeat in PostgreSQL; `/ready` exposes the last successful scan and the production readiness monitor fails if the heartbeat is missing, unsuccessful or older than 20 minutes.
- PostgreSQL CI verifies the expiry receipt and persisted worker heartbeat.

This implements expiry of the short-lived privacy export archive only. It does **not** complete the retention schedule or prove deletion from database backups, provider logs, replicas or external processors.

## 5. Engineering enforcement required

Before marking the schedule enforced, implement and test:

- A versioned data-class policy registry with approved period, trigger, legal basis/authority, owner and policy version.
- Database-backed expiry/deletion jobs with idempotency, bounded batches, retries, dead-letter visibility and audit events.
- Referential dependency handling across person/caregiver relationships, source evidence, document bytes, extraction candidates, derived records and exports.
- Explicit legal-hold and in-progress-request exclusions.
- Provider deletion/expiry actions and confirmation evidence for each external processor.
- Backup lifecycle policy that defines when deleted records age out of recoverable backups and what happens if a restore occurs before expiry.
- A post-restore deletion replay/reconciliation mechanism so a restored backup cannot silently resurrect data whose deletion was already completed.
- Negative tests proving another caregiver's records and unrelated shared-care data are not deleted.
- A deletion verification report that contains identifiers and outcome metadata only, not the deleted care content.

Do not add a scheduled destructive purge until policy periods, dependencies, legal holds and restore behavior have been reviewed and tested. A guessed retention period can cause irreversible loss of care history or unlawful over-retention.

## 6. Required approval and evidence

The Founder must obtain qualified privacy/legal review for applicable jurisdictions and document the approved periods. Engineering must then map each row to exact tables, object stores, logs, caches, providers and backup systems. Keep approvals and production evidence in a restricted operations system, not public source control.

**Release gate:** Until the schedule is approved and enforced, mark retention/deletion maturity BLOCKED. Do not tell users that SolenOS automatically deletes all personal or care data after a stated period.
