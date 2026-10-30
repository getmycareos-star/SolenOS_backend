# Messy-care benchmark acceptance contract

This is the primary engineering acceptance gate for changes that ingest, reconcile, persist, retrieve, summarize, or explain care history. Unit tests and mocked inference are not sufficient evidence.

## Required invariants

1. **Speaker attribution:** distinguish the authenticated uploader, reporting caregiver, observer/collector, original author, and care subject. Missing or ambiguous identity remains unknown.
2. **Contradictions:** preserve conflicting source assertions independently, with provenance and status. A later assertion must not silently overwrite a conflict.
3. **Correction lineage:** corrections are append-only, linked to the superseded assertion, and retrievable with actor, reason, effective/event time, and recorded time.
4. **Thread isolation:** unrelated care concerns never merge because of a matching label, generic subject name, or nearby timestamp. Joining requires explicit source lineage and exact person/domain/subject scope.
5. **Temporal reconstruction:** event/effective time is distinct from report/collection/recorded time. Backdated and late-arriving evidence must not rewrite what was known at an earlier as-of time.
6. **Unresolved questions:** unanswered questions remain retrievable after fresh sessions and outside recent-history windows; answered questions retain answer provenance.
7. **Persistence:** verify write/read in a fresh SQLAlchemy session against PostgreSQL 17. The mandatory workflow then restarts the PostgreSQL server process and verifies a persisted row remains readable.
8. **Security:** evidence, contradictions, corrections, questions, situations, and private Companion conversations enforce person and caregiver authorization. Cross-person and unauthorized cross-caregiver reads fail closed.
9. **Explanation grounding:** every material care-history claim includes exact persisted source references. Missing evidence remains unknown; fabricated links and unsupported completion or clinical conclusions fail.

## Mandatory benchmark evidence

- API-level multi-source ingestion and fresh-session speaker/time attribution.
- Contradictory evidence preservation and contradiction status history.
- Correction and reopening history with the original state retained.
- Source-scoped situation grouping adversarial tests.
- Historical unresolved-question retrieval and answer provenance.
- PostgreSQL 17 integration tests, migration to head, fresh-session retrieval, and post-restart persistence.
- Cross-person and cross-caregiver authorization tests.
- Source-linked handoff and care-continuity explanation tests.

## Release gate

- Run on every pull request to `main` and every push to `main`.
- PostgreSQL 17 is mandatory; the integration tests must not skip due to missing `DATABASE_URL`.
- Zero skipped cases, zero failures, zero errors, and nonzero executed tests in both benchmark jobs.
- Retain JUnit artifacts and record exact tested commit SHA.
- Any privacy breach, lost contradiction/correction, incorrect attribution, false as-of reconstruction, unsupported completion, or fabricated evidence reference is a hard failure.
- A missing test for a required invariant is a coverage gap, not a pass.

Synthetic benchmark fixtures are not production caregiver data. Passing this gate does not by itself establish field reliability with real families; that requires separately governed, consented, de-identified validation.
