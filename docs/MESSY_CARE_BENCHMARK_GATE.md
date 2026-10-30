# Messy-care benchmark gate

## Purpose

The messy-care benchmark is the primary engineering acceptance gate for changes that ingest, interpret, reconcile, persist, retrieve, summarize, or explain care history.

A capability is **not complete** because a route exists, a unit test passes, a model produces plausible text, a migration succeeds, or a PR description says the work is implemented. Completion requires an end-to-end result against independently labelled adversarial histories and durable database state.

## Current gate

The GitHub Actions workflow `.github/workflows/situation-understanding-benchmark.yml` runs on every pull request targeting `main`, every push to `main`, and manual dispatch. It executes:

- Adversarial situation-understanding and uncertainty contracts.
- Longitudinal event and change-detection cases.
- Open Loop messy-care workflow cases.
- Temporal provenance and evidence-lineage/review cases.
- Care-continuity link validation.
- The longitudinal persistence suite against a real PostgreSQL 17 service.

The workflow fails on missing reports, zero executed tests, failures, errors, or skipped tests. JUnit reports are uploaded as workflow artifacts. A passing result means only that the currently included synthetic contract and PostgreSQL tests passed on that exact commit. It is not a clinical validation result or proof of production performance.

## Required end-to-end proof for a capability

For each claimed capability, tests must exercise the complete applicable path:

1. **Ingest:** accept the original caregiver statement, document, event, or other source through the real API; preserve the original bytes/text and source metadata.
2. **Reconcile:** associate related statements without collapsing distinct concerns; preserve disagreement, uncertainty, late-arriving evidence, duplicates, and corrections.
3. **Persist:** commit the result to PostgreSQL with stable identifiers, source lineage, effective/recorded timestamps, actor attribution, and append-only correction history where applicable.
4. **Retrieve:** retrieve the same history after a fresh database session and application restart; enforce person/caregiver authorization and prove cross-record denial.
5. **Explain:** return a bounded, source-linked explanation that distinguishes observed facts, reports, inferences, contradictions, missing evidence, and unresolved questions. Every cited source must exist in the authorized record and support the statement attributed to it.

A test that stops at a pure function or mocked repository boundary cannot establish end-to-end capability. Unit tests remain useful but are supplemental.

## Adversarial case requirements

The benchmark corpus must expand to cover, at minimum:

- Multiple caregivers reporting the same event with different details.
- Direct contradictions and later corrections without erasing the original report.
- Several simultaneous, unrelated care threads.
- Repeated occurrences versus duplicate copies of one occurrence.
- Late-arriving evidence and backdated observations.
- Conflicting effective time and recorded/knowledge time.
- Missing, partial, stale, unreadable, or inaccessible documents.
- Copied summaries that must not count as independent corroboration.
- Similar names, ambiguous pronouns, and incorrect speaker/reporter attribution.
- Planned versus completed appointments and actions.
- Unknown observation coverage and documentation bursts.
- Cross-person and cross-caregiver isolation attempts.
- Retrieval of old but relevant history outside recent-record windows.
- Restart, retry, concurrency, and idempotency behavior.

Each scenario must have expected outcomes fixed independently of the implementation being tested. Expected labels must not be generated from the system output.

## Hard safety gates

No aggregate score can compensate for any of these failures:

- Cross-person or unauthorized data disclosure.
- Fabricated, inaccessible, or wrong-record evidence references.
- Erasure or silent replacement of a contradictory report or correction.
- Treating missing/inaccessible evidence or silence as proof that an event did not occur.
- Converting a plan, action, or unverified report into a completed/verified outcome.
- Unsupported causal, diagnostic, medication, or future-risk claims.
- Presenting an inference as an observed fact or hiding material uncertainty.

Any such failure fails the benchmark.

For non-critical structured fields, report per-dimension precision/recall and exact-match rates. Do not publish one blended accuracy number that hides a safety-critical miss. Report false positives and false negatives separately, and retain per-case outcomes.

## Evidence required before marking complete

The PR must include:

- The exact benchmark commit SHA and workflow run URL.
- The test corpus version/hash and independently authored expected labels.
- Per-case results and per-dimension metrics.
- PostgreSQL persistence/retrieval evidence, including fresh-session or restart reads.
- Authorization and cross-record isolation results.
- Explicit failures, exclusions, limitations, and skipped-case count (which must be zero).
- A statement distinguishing synthetic benchmark evidence from any separately consented, de-identified real-world evaluation.

No production caregiver data may be copied into a benchmark corpus. Real-world evaluation requires appropriate consent, de-identification, access controls, and a documented review process.

## Current status

This workflow change makes the existing synthetic adversarial and PostgreSQL longitudinal suites run on every PR instead of only selected path changes. It is the first enforcement step, not proof that the complete end-to-end benchmark corpus described above is finished. Until the full ingest → reconcile → persist → retrieve → explain corpus and its required evidence exist and pass, broader messy-care capability claims remain **unverified**.
