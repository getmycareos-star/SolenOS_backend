# Freshness migration and Open Loop recovery

## Scope

This change is limited to the following correctness and verification work on backend `main`:

1. Diagnose and repair the exact failure in Alembic revision `20261029_25_freshness` without dropping, truncating, rebuilding, or silently rewriting existing tables or rows.
2. Prove migration safety against:
   - a clean PostgreSQL database upgraded from the true base revision to head;
   - a database where ORM/create_all has already created the freshness tables;
   - a partially populated legacy schema with existing policy and assessment rows;
   - a repeated/partially applied migration state, where supported.
3. Verify preserved row counts, primary keys, foreign keys, unique policy-version enforcement, indexes, and append-only protections. Incompatible schemas must fail with an explicit diagnostic rather than destructive repair.
4. Expand Open Loop PostgreSQL tests to cover genuinely concurrent same-key creates, competing lifecycle transitions, idempotent replay, conflicting key reuse, and transaction/locking outcomes.
5. Run the complete backend test suite, PostgreSQL migration validation, PostgreSQL HTTP persistence/concurrency tests, and production-container smoke test against one exact candidate commit.
6. Record warnings separately from failures. Warnings are not silently reclassified as passes; each warning must be assessed for correctness, safety, and production impact.

## Required Open Loop concurrency cases

- Two independent requests create the same logical loop with the same idempotency key at the same time: exactly one durable logical record/event is created and both callers receive the documented replay result.
- Same key with different payloads: deterministic conflict; no second record and no silent replay of different content.
- Concurrent close versus cancel, close versus reopen, and other supported competing terminal transitions: one valid serialized outcome; the losing request receives a deterministic conflict or documented replay.
- Concurrent outcome recording/verification and closure: closure cannot bypass required verification or evidence checks.
- Verify event history, version progression, and persisted state after process restart.

## Acceptance gates

All gates must run against the same exact candidate SHA. A result from an earlier commit does not count.

- [ ] Root cause of the freshness migration failure documented with the failing SQL/state.
- [ ] Forward-only, non-destructive migration repair implemented.
- [ ] PostgreSQL preservation and compatibility tests pass.
- [ ] Expanded Open Loop concurrency/lifecycle tests pass against real PostgreSQL.
- [ ] Full backend suite passes.
- [ ] PostgreSQL migration validation passes.
- [ ] PostgreSQL HTTP persistence/concurrency tests pass.
- [ ] Container build and smoke test pass.
- [ ] Warnings and failures reported separately with disposition.
- [ ] Final candidate SHA and all run URLs recorded.

## Safety boundary

Do not deploy to production or merge automatically. Do not modify existing pull requests. Do not claim completion from SQLite-only tests, mocks, schema inspection, or workflow configuration. Keep this PR draft until every required gate is green on its exact head.
