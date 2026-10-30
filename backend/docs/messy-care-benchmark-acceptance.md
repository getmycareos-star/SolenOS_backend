# Messy-care benchmark acceptance contract

This is the primary engineering gate for changes that ingest, reconcile, persist, retrieve, summarize, or explain care history. Unit tests and mocked inference are not sufficient.

## Nine mandatory invariants

1. **Speaker attribution:** distinguish authenticated uploader, reporting caregiver, observer/collector, original author, and care subject. Missing or ambiguous identity remains unknown.
2. **Contradictions:** conflicting source assertions remain independently addressable with provenance and status. New evidence cannot silently overwrite a conflict.
3. **Correction lineage:** corrections are append-only and linked to superseded assertions. Historical reads retain original assertion, every correction, actor, reason, event/effective time, and recorded time.
4. **Thread isolation:** unrelated care concerns never merge because of matching labels, generic subject names, or nearby timestamps. Joining requires explicit shared source lineage and exact person/domain/subject scope.
5. **Temporal reconstruction:** event/effective time is distinct from report/collection/recorded time. Backdated and late evidence cannot rewrite what was known at an earlier as-of time.
6. **Unresolved questions:** open questions remain retrievable after fresh sessions and outside recent-history windows; answered questions retain answer provenance.
7. **Persistence:** PostgreSQL 17 integration tests verify writes and fresh-session reads. The mandatory workflow restarts the PostgreSQL server process and verifies persisted data remains readable.
8. **Security:** evidence, contradictions, corrections, questions, situations, and private Companion conversations enforce person and caregiver authorization. Cross-person and unauthorized cross-caregiver reads fail closed.
9. **Explanation grounding:** every material claim includes exact persisted source references. Missing evidence remains unknown; fabricated links and unsupported completion/clinical claims fail.

## Mandatory evidence

- API-level multi-source ingestion and fresh-session speaker/time attribution.
- Contradictory evidence preservation and contradiction status history.
- Correction and reopening history with original state retained.
- Source-scoped situation grouping adversarial tests.
- Historical unresolved-question retrieval and answer provenance.
- PostgreSQL 17 integration tests, migrations to head, fresh-session reads, and post-restart persistence.
- Cross-person and cross-caregiver authorization tests.
- Source-linked handoff and care-continuity explanation validation.

## Release gate

- Run on every pull request to `main` and every push to `main`.
- PostgreSQL 17 is mandatory; integration tests must not skip due to missing `DATABASE_URL`.
- Zero skipped cases, zero failures, zero errors, and nonzero executed tests in both benchmark jobs.
- Retain JUnit artifacts and record exact tested commit SHA.
- Any privacy breach, lost contradiction/correction, incorrect attribution, false as-of reconstruction, unsupported completion, or fabricated evidence reference is a hard failure.
- A missing test for a required invariant is a coverage gap, not a pass.

Synthetic fixtures are not production caregiver data. Passing this gate does not establish field reliability with real families; that requires separately governed, consented, de-identified validation.
