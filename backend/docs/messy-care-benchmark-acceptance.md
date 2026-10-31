# Messy-care benchmark acceptance contract

This contract is the primary engineering gate for care-history correctness. A green unit suite is not sufficient unless the API and persisted record are exercised across a fresh session and, where stated, a PostgreSQL process restart.

## Non-negotiable invariants

1. **Speaker attribution** — uploader, reporter, observer/collector, original author, and care subject are distinct roles. Missing or ambiguous identity remains unknown; it is never inferred from the authenticated uploader.
2. **Contradictions** — conflicting assertions remain independently addressable with their own source and status. New evidence cannot silently overwrite an incompatible assertion.
3. **Correction lineage** — a correction is append-only and points to the superseded assertion. Historical reads can retrieve the original, each correction, actor, reason, event time, and recorded time.
4. **Thread isolation** — signals are joined only with explicit source lineage and exact person/domain/subject scope. Shared labels, timestamps, or generic subject names are not identity.
5. **Temporal reconstruction** — event/effective time and recorded/ingested time are separate. Backdated and late-arriving evidence must not rewrite what the system knew at an earlier as-of time.
6. **Unresolved questions** — open questions survive compaction and fresh-session retrieval; older relevant questions cannot disappear only because they fall outside a recent-item window.
7. **Persistence** — write/read is verified in a new SQLAlchemy session and against PostgreSQL 17; restart tests must reconnect and reconstruct from persisted rows, not in-memory caches.
8. **Security** — every evidence, question, correction, and situation read/write is person-scoped and caregiver-authorized. Cross-person and unauthorized cross-caregiver access must fail closed.
9. **Explanation** — each material claim returned to the caregiver carries exact persisted source IDs and provenance. Missing evidence is reported as unknown; unsupported conclusions and fabricated source links fail the benchmark.

## Required test evidence

- Multi-source API ingestion and source attribution: `test_situation.py`, `test_longitudinal_messy_care_benchmark.py`, and evidence-lineage tests.
- Contradiction and correction preservation: `test_uncertainty.py`, `test_evidence_lineage.py`, and uncertainty API benchmark.
- Thread isolation: `test_situation_temporal_provenance.py` and end-to-end situation reconstruction tests.
- Temporal reconstruction: provenance and longitudinal benchmark tests.
- Historical unresolved questions: situation reconstruction, uncertainty API and open-loop tests.
- Persistence: PostgreSQL longitudinal benchmark, pattern PostgreSQL integration, and fresh-session reads.
- Authorization: anonymous-session, uncertainty API, evidence, appointment workspace and open-loop cross-person tests.
- Explanation/source grounding: care-continuity link validation, LCR context, situation and evidence review tests.

## Release gate

- Run the complete benchmark on every push to `main` and by manual workflow dispatch. Pull-request and feature-branch runs are intentionally excluded; qualification is performed against the canonical `main` commit.
- PostgreSQL 17 is mandatory for the persistence job.
- Zero skipped cases, zero failures, zero errors, nonzero executed tests.
- Retain JUnit artifacts and report exact commit SHA.
- A test suite that does not exercise a required invariant is a coverage gap, not a pass.
- Any privacy breach, lost contradiction/correction, incorrect attribution, false as-of reconstruction, unsupported completion, or fabricated source reference is a hard failure.

Synthetic fixtures are not production caregiver data. Synthetic benchmark success does not establish real-world clinical or caregiver reliability without separately governed field validation.
