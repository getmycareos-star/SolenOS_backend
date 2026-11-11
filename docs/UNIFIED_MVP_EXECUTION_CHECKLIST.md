# SolenOS unified MVP — execution checklist

**Scope:** existing Solenos MVP only. Community is intentionally disabled and excluded from this work.
**Baseline:** backend `main` and frontend `main`; no feature-branch assumptions.
**Architecture:** FastAPI modular monolith + PostgreSQL; Next.js frontend; existing Netlify/Railway deployments.
**Objective:** one person-scoped, evidence-linked care history shared by capture, LCR, Companion, documents, reminders and handoffs.

## Release rules

- [ ] No new product features during this work.
- [ ] No microservices, database replacement, frontend rewrite or broad refactor.
- [ ] No destructive migrations; every schema change must be additive/backward-compatible and have a rollback.
- [ ] Community remains disabled; do not mount, expose, test or connect community flows as part of this project.
- [ ] A capability is not complete because its route, model or unit test exists. It must pass an end-to-end PostgreSQL test.
- [ ] Every write is scoped from the server-issued session; never trust a browser-supplied person or caregiver ID as authorization.
- [ ] Original caregiver reports and source documents are immutable; corrections append lineage.
- [ ] AI output remains a proposal until the required caregiver review; no model-only assertion becomes authoritative.
- [ ] Do not report a task complete without a passing check and a linked commit/workflow result.

## Strict execution order

### 0. Freeze and baseline
- [ ] Record exact backend and frontend `main` SHAs before each implementation slice.
- [ ] Inventory active UI entry points and the actual API calls they make.
- [ ] Inventory all backend write paths for care events, evidence, assertions, documents, reminders and handoffs.
- [ ] Identify duplicate/legacy routes; classify each as active, compatibility-only or unused.
- [ ] Do not remove anything until usage is proven absent.

**Exit:** one verified map from each active UI action to its backend route, owning service, tables and read projection.

### 1. Canonical capture and evidence write path
- [ ] Make Daily Care capture create one durable CareEvent and one immutable Evidence source, linked by IDs.
- [ ] Preserve reporter, uploader, collector, event time, recorded time, channel and epistemic status separately.
- [ ] Enforce person/caregiver scope from the bearer session.
- [ ] Enforce idempotency: same key + same payload returns the original receipt; same key + changed payload returns 409.
- [ ] Ensure secondary AI/learning/reconciliation failures cannot turn a committed capture into a false save failure.
- [ ] Route other caregiver-authored capture entry points through the same domain write service; do not duplicate persistence logic in routers.

**Exit:** PostgreSQL HTTP test proves one capture produces exactly one event and one linked source-evidence row, survives a new DB session, and rejects cross-person access and conflicting retries.

### 2. One LCR projection
- [ ] Keep one canonical read/projection contract for care events, evidence, reviewed assertions, documents, open loops and outcomes.
- [ ] Remove duplicated aggregation logic from UI-specific routes; delegate to the canonical projection.
- [ ] Preserve source IDs, event time vs recorded time, author/reporter, epistemic status, corrections and conflicts.
- [ ] Return explicit missing-source/schema coverage; never present a partial projection as complete.
- [ ] Ensure bounded retrieval reports truncation and never silently implies that omitted history does not exist.

**Exit:** the same persisted capture is represented once, with the same event/evidence IDs and provenance, in LCR and the shared retrieval contract.

### 3. Companion consumes the same care context
- [ ] Use the canonical LCR retrieval contract for Companion care-history retrieval.
- [ ] Keep conversation messages as conversational context, never care evidence.
- [ ] Align returned source IDs exactly with the records actually supplied to the model.
- [ ] Ensure source attribution and uncertainty survive prompt construction and response persistence.
- [ ] Verify a capture from an earlier session is retrievable by the authorized caregiver in a later session.

**Exit:** PostgreSQL test proves Daily Care → persisted event/evidence → new-session Companion retrieval, with no foreign-person or unauthorized-caregiver leakage.

### 4. Documents join the same evidence lineage
- [ ] Persist the original upload as evidence before extraction.
- [ ] Link every extraction and extracted observation to its source evidence and source spans where available.
- [ ] Keep extracted/unreviewed claims distinct from caregiver-reviewed assertions.
- [ ] Make document evidence visible through the same LCR projection and Companion retrieval contract.
- [ ] Verify failed extraction never deletes or invalidates the original upload.

**Exit:** upload → extraction → LCR → Companion retrieval works in PostgreSQL with source linkage and review status intact.

### 5. Reminders, open loops and handoffs use canonical context
- [ ] Verify each reminder/open loop is scoped to the correct person and linked to source evidence where applicable.
- [ ] Preserve status transitions and completion outcomes as history.
- [ ] Build handoff drafts from the canonical LCR projection, not a separate care-history implementation.
- [ ] Include unresolved questions, uncertainty and relevant source references.
- [ ] Do not mark a handoff delivered or an action completed without a persisted receipt/outcome.

**Exit:** a saved care event can be referenced by the relevant open loop/reminder/handoff and remains traceable to its original evidence.

### 6. Frontend integration cleanup
- [ ] Keep one API client/session authority.
- [ ] Remove duplicate frontend fetch/state paths only after their replacements are verified.
- [ ] After every successful write, refresh the affected LCR projection from the backend.
- [ ] Never show “saved”, “synced” or “updated” before the server confirms persistence.
- [ ] Preserve user input and provide retry after network failure without duplicate writes.
- [ ] Verify direct navigation, refresh, new session and mobile viewport for the core workflow.

**Exit:** Daily Care, LCR, Companion, Documents, Reminders and Handoffs all consume the same backend identity and care-data contracts.

### 7. Final release gate
- [ ] Run backend unit, integration, PostgreSQL migration and messy-care benchmark workflows on the final commit.
- [ ] Run frontend typecheck, lint/build and production smoke checks on the final commit.
- [ ] Verify Netlify frontend and Railway backend are running the intended commits.
- [ ] Execute the caregiver scenario below against the deployed MVP.
- [ ] Record failures as blockers; do not expand scope to explain them away.

**Exit:** all checks green, deployment SHAs confirmed, and the deployed workflow succeeds end to end.

## Single acceptance scenario

A caregiver records: “Mum was unusually confused this afternoon. She also refused lunch.”

The deployed system must:
1. Save one event and one linked, immutable caregiver-report evidence item against the correct person.
2. Preserve the caregiver as reporter and distinguish event time from recorded time.
3. Keep the report caregiver-reported, not clinically verified.
4. Show the same event and evidence IDs in LCR.
5. Make the same source retrievable by Companion in a later session, with provenance.
6. Preserve the original report if a later caregiver corrects or contradicts it.
7. Reconstruct the same history after refresh, a new database session and backend restart.
8. Deny access from a different person's session.

**Anything outside this acceptance path is out of scope until this passes.**
