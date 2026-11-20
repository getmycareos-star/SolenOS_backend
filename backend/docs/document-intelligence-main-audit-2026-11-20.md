# Document Intelligence — main implementation audit (2026-11-20)

## Scope and baseline

- Repository: `getmycareos-star/SolenOS_backend`
- Authoritative branch: `main`
- Exact starting main commit observed before this implementation: `de631704ed2c0d7ee0ac88d7d5aca8a574cfe466` (`test(handoff): cover snapshot scope metadata integrity`, 2026-11-20 06:10:31 UTC).
- Implementation commits created on `main`:
  - `c36dcbf9e9177433dded729116e04cece9f97d6d` — preserve explicit symptom/medication negation.
  - `64b6915e17edbe1648474f0d477e9ee609337b3c` — adversarial negation/temporal regression tests.
  - `cec6893502aa2364f8b7f86b039f3b2210655815` — include the adversarial suite in the live-Qwen workflow path filter.
  - `f16fb02c91a1791201e8a461cbfd8e35ed4148ef` — run document semantic/lifecycle regression tests before live-Qwen evaluation.
- No migration was added or modified in this slice.
- No production deployment was requested or performed.

## Existing implementation map

| Concern | Existing owner | Current evidence / boundary |
|---|---|---|
| Original binary and integrity | `backend/app/api/document_intelligence.py::_source`; `backend/app/api/document_lifecycle.py::_verified_content`; `EvidenceUpload` | Rechecks byte length and SHA-256 before extraction/review. Exact duplicate uploads retain provenance and are not independent corroboration. |
| Extraction and OCR | `backend/app/api/document_lifecycle.py` | PDF text, scanned-page OCR, image and DOCX adapters; page markers, unreadable/retryable/blocked page states, bounded extraction. |
| Candidate schema | `backend/app/services/document_candidate_schema.py` | Exact quote/page/span, field classification, attribution, negation, modality, conditionality, temporal context, date and medication warnings. |
| Qwen candidate generation | `backend/app/api/document_intelligence.py::analyze_saved_document`; `backend/app/services/qwen_gateway.py` | Uses persisted extraction and Qwen gateway. Output is review-only; source quote must be grounded in persisted source text. |
| Review lifecycle | `CandidateReview`, `review_document_candidate`, `DocumentReviewEvent` | Accept/correct/reject; corrected candidates require a second approval; event history is append-only; rejected candidates do not create assertions. |
| Review persistence migration | `backend/alembic/versions/20261106_01_document_review_events.py`; forward repair `20261107_03_repair_doc_review_events.py` | Creates/reconciles the append-only review event table and database mutation guards. The full live database migration chain still requires an exact-main run. |
| Canonical assertion and Delta | `create_assertion`, `execute_care_continuity_reconciliation`, `reconcile_document_delta` in document review | Accepted assertions are source-linked and reconciled through the existing shared coordinator; no direct parallel LCR store. |
| LCR retrieval | `build_lcr_context` and shared retrieval | Review path verifies the accepted assertion is visible in the caregiver-scoped LCR projection. |
| Companion retrieval | Existing Companion/Qwen context and `backend/live_tests/test_document_intelligence_live.py` | Live test expects source evidence, analysis and accepted assertion IDs in Companion response. |
| PostgreSQL lifecycle | `backend/tests/test_document_intelligence_postgres.py` and `ci/run-document-lifecycle-postgres.sh` | Uses real PostgreSQL for upload, durable job, extraction, review, LCR, Delta, identity isolation and restart coverage; Qwen is deliberately stubbed at the inference boundary in this suite. |
| Real model evaluation | `backend/live_tests/test_document_intelligence_live.py`; `.github/workflows/companion-live-inference.yml` | Dedicated Ollama/Qwen + PostgreSQL suite; does not mock candidate inference. Uses a manually labelled synthetic engineering dataset, not clinical validation. |

## Changes in this slice

The deterministic negation vocabulary now recognizes explicit phrases including `not experiencing`, `not taking`, `not using`, and `without evidence of`, in addition to existing denial/negation markers.

A new parameterized test module covers:
- No longer requires assistance.
- Previously diagnosed with.
- Medication discontinued.
- Medication dosage changed.
- Patient denies experiencing pain.
- Condition resolved last month.
- Current medication differs from discharge medication.
- Explicitly not experiencing pain.
- Explicitly not taking medication.

The live-Qwen workflow now runs candidate-schema, adversarial-candidate and document lifecycle regression tests before attempting the real model evaluation.

## Verification state — deliberately conservative

- Source changes and workflow configuration are committed to `main`.
- The repository's existing PostgreSQL integration test uses a deterministic Qwen stub by design. It verifies database/API lifecycle, not model quality.
- A separate live-Qwen workflow and synthetic semantic evaluation dataset exist. Their existence is not evidence that this new commit has passed them.
- The GitHub connector currently exposes Railway deployment status contexts for the inspected commits, but no GitHub Actions workflow-run details were returned. Railway success contexts are not treated as pytest, PostgreSQL migration, or real-Qwen test evidence.
- No local test runner or connected PostgreSQL/Ollama runtime was available through this repository-editing session. Therefore no test is reported as executed or passed here.
- No claim is made about production extraction accuracy, clinical accuracy, real deployed Qwen inference, or post-restart durability on the live service.

## Remaining execution gates

1. Run the exact-main candidate-schema, adversarial and lifecycle test suites; fix any failing expectations.
2. Run `alembic upgrade head` from a clean PostgreSQL database and from a database carrying the documented review-table repair scenario.
3. Run `test_document_intelligence_postgres.py` against PostgreSQL, including the configured restart test; verify accept/correct/reject, append-only history, idempotency, Delta reconciliation and fresh-session LCR retrieval.
4. Run `backend/live_tests/test_document_intelligence_live.py` with the configured `qwen3.5:4b` Ollama runtime and PostgreSQL. Record actual output and per-case metrics; do not infer model success from model inventory.
5. Verify deployed Railway revision, model readiness and authenticated production inference separately before any production acceptance.
6. Exercise Companion retrieval for accepted, historical and conflicting document assertions; verify citations and person isolation.

## Safety boundary

Unreviewed candidates remain proposals. Rejected candidates must not become canonical assertions. Medication document conflicts remain unresolved until the appropriate professional confirmation; document extraction must never be interpreted as medication-change advice. Synthetic semantic labels are engineering tests, not clinical accuracy claims.
