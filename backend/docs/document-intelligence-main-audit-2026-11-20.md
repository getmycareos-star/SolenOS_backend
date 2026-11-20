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

## Deployment boundary observed during this implementation

The Railway project `SolenOS` production backend was inspected read-only:
- Service: `SolenOS_backend` (`69c367dd-7e01-4327-bb3b-bbb25af60f87`).
- Latest live deployment at inspection: `SUCCESS`, commit `e0166fe8f99bf12944956f8a5f86d93fa2c1c292`, created 2026-11-19 06:37:26 UTC.
- The live source configuration was pinned to that older commit.
- A pre-existing staged Railway patch was present, with five staged changes and target source commit `893913b11edf109138d2541ad5895910ba3fedd1`; it was not created or committed by this implementation.
- This implementation did not accept staged changes, trigger a Railway deployment, restart the service, or alter production variables.
- Consequently, the commits in this audit are on GitHub `main`; they are not claimed to be running in the production service.

## Continued implementation on main — 2026-11-20

Latest main commit observed during the continued implementation: `ad853cf6f2b86dcbe346d0d93f18fe371fd81164`.

Additional document-specific commits:
- `17547f9fb63c41a7a283dd46f5c9006caee1edeb` — API-level exact duplicate uploads reuse one accepted LCR assertion and retain separate review-event provenance.
- `d17791299e57bc225007d9d03a47b25f56284eee` — PostgreSQL advisory transaction lock around exact duplicate source review.
- `9148cd2c60c7ea19b4430160f3b48614ac5dd26d` — PostgreSQL concurrent duplicate-review regression test.
- `4c22ad23c54780574a24663325a722decf75ed40` — workflow now runs the PostgreSQL document persistence/review/duplicate/restart suite with `SOLENOS_TEST_POSTGRES_RESTART=1`.
- `4258ad90e21fa1fab116807391eba42e736b10f0` — duplicate-source idempotency key widened to source identity; contradictory field/subject reinterpretations of identical binary+quotation are blocked rather than silently creating another assertion.
- `dbfd6eb074598c73d00abf93b51aaf74591c0367` — regression test for changed field interpretation on exact duplicate source.
- `edfbf0ab649bff55344efb21b26a97613f592821` — regression test proving a later correction request cannot overwrite an already accepted assertion.
- `f516da2ef3f6d7bf9b85dcee1245bf22569d722f` — live-Qwen Companion cross-person document-source isolation assertion.

Related main commits supporting the same end-to-end objective:
- `3e8a315a844ca03f5c9df57aed94e77f3f247f95` — Companion prompt serialization now wraps every LCR record with explicit `source_type` and `source_id`, preserving typed provenance through model context.
- `2396ad2e0f525cc49bf341c24875903464f90691` — regression test for typed LCR source identity and retrieval receipts.
- `f10111519d9ae4aba3b0df18b8ba246cf64ee412` — care-state revision/invalidation table coverage extended to Core-written longitudinal and document review tables.

### Duplicate-source correctness change

The duplicate assertion lookup previously narrowed its search by proposed field, subject and assertion kind. That could miss a second interpretation of the same exact uploaded bytes and source quotation if model classification changed between analyses. The lookup now:
1. Acquires a PostgreSQL transaction advisory lock keyed by person, original binary SHA-256 and exact source quotation.
2. Searches existing assertions for that person and exact quotation across fields/subjects.
3. Restricts the candidate assertions to evidence IDs in the exact-binary duplicate group.
4. Reuses only an assertion whose interpretation and typed identity also match.
5. Rejects a conflicting interpretation with HTTP 409 instead of creating a second contradictory assertion.

Different documents with different binary hashes are not collapsed by this duplicate guard; they remain eligible for normal LCR contradiction reconciliation.

### Added verification coverage

- Sequential duplicate document uploads with matching interpretations resolve to one LCR assertion and two source-specific append-only review events.
- An exact duplicate with a different proposed field interpretation is rejected; it cannot create a second assertion.
- PostgreSQL concurrency test exercises two simultaneous reviews against separate analyses for the same duplicate source.
- An accepted assertion cannot be overwritten by a subsequent correction request.
- Live-Qwen Companion test asserts another person's authenticated session cannot retrieve the first person's document evidence, analysis or accepted assertion identifiers.
- The live-Qwen workflow now invokes the PostgreSQL document integration test, including an explicit PostgreSQL service restart.

### Execution status

These changes and test definitions are committed on `main`. The CI workflow is configured to execute the PostgreSQL and real-Qwen suites on relevant main pushes. A successful workflow run, test counts, per-case live-Qwen metrics and production deployment revision have not yet been independently confirmed; no passing result is inferred from the commits or Railway deployment status contexts.

- `72dad4c067e2dde7b6d0117b3b85c6644506847a` — two separate discharge documents with conflicting Donepezil strengths are both retained as source-linked assertions.
- `411f1b9dff7f674efd42f495e55c0d38a114b20a` — verifies both conflicting document assertions and their unresolved contradiction remain retrievable from the canonical LCR projection.

- `9413c5236c3664c0f2e8fc4ef21b3cac395954f0` — added a manually labelled tentative-condition case to the synthetic semantic gold dataset.
- `4fffd1f4ecfc3b0060617d915a5bad8ca0e2652b` — live-Qwen Companion evaluation now accepts and retrieves historical and tentative assertions, checking that the response retains the historical/tentative qualifiers.
- `b583ce035c931f518a0d73b9ea36e8bc7f6e097b` — live-Qwen duplicate-upload test reprocesses the exact same binary and verifies the second accepted review reuses the original assertion and creates no duplicate assertion.
- `13f56cedeb1eb3231282050ec262e5cacecdfb0e` — live-Qwen review tests select mobility and rejection candidates by exact source passage rather than relying on model output ordering.
- `0deea7eb6309cea927a7608ed8218cde9ea53014` — historical Companion evaluation accepts past-tense wording variants.

- `ca54866ebfebb10538ae4a18e9ef33ea9a0c87f6` — dedicated live-Qwen workflow now installs Tesseract and runs the real document-format extraction suite (native/scanned PDF, images, DOCX, RTF, retries and page provenance) before model evaluation.
- `1948726a71d3aef6fc441df8f00b3727ce54a7c8` — changes to document format tests and semantic fixtures now trigger the live document evaluation workflow.

- `3bbe83580f2482818e9f326a32d5b90893a88a5c` — added a second real-Qwen live workflow covering two separately uploaded, conflicting Metformin documents; both accepted assertions must survive in the LCR and Companion must retrieve and explain the unresolved conflict.
- `8fbcc8929d3cf34af84ea4366a73cb7038af5faf` — live conflict evaluation tolerates the exact source quotation being embedded in a larger verbatim passage.
- `7f86d1e28c33bd6031a56eeda6cde6792c5ee47f` — deterministic temporal metadata now recognizes the verb "suspects" as tentative language, not only "suspected".
- `c8378886cbb1fb2502ad490b0e9fffba24cd8bcc` — adversarial regression for "The clinician suspects an infection."
- `42bfe188d409ce153639a2b0d2b26e738b65c65b` — semantic gold case isolates the suspicion verb without the word "possible".
- `eae8b0cffcfa29e0544612b898ea3b8db96003fb` — live-Qwen metrics now calculate modality accuracy, source-quotation accuracy and unsupported-candidate rate from observed candidates instead of hardcoding those metrics.

- `cb06d3a0219f1a28ad2dfac61566fffc05f63c47` — live conflict workflow now sends a scanned PDF through actual Tesseract OCR and then real Qwen candidate generation before review and LCR reconciliation.
- `8fcfa6d998a94da45b91b23e4d0b00d42ce68e7f` — the second conflicting medication document is now a generated DOCX, so the same live-Qwen test covers scanned-PDF OCR and DOCX extraction through to Companion conflict retrieval.

- `0a61ed917a131f245fe640f598cbdbf9b9a2369d` — accepted document-derived CareAssertions are now projected into the existing `GET /api/situation` response with exact quotation, source evidence/document ID, page, reviewer, temporal, negation, modality and epistemic metadata.
- `0c8ca3a201b021586856bac5e2f9738fbda3d6f1` — the situation projection recognizes all caregiver-approved document assertions, including medication-status assertions that deliberately remain `reported` rather than clinically verified.
- `54e01b84ef09866d333d21d8dea770e7dc17cdf8` — the same accepted LCR assertions are projected into the existing `SituationObservation` shape (`kind=document`, source reference, reported epistemic state) consumed by the frontend Progressive Understanding Engine; no new evidence table or write path was added.
- `8d2d1a005ceef70f683af3caffe6961c6fb97765` — regression coverage proves pending candidates do not appear in Progressive Understanding, while caregiver-approved assertions do.
- `fbbb7127d6f4dff6c98d2877cab2a3cea82cb5d2` — aligns document source statement attribution values with the existing Progressive Understanding observation contract.

- `2d85850740d0be375275b7015e55187f48be76a9` and `1fd788b055bcf78d0e8648e57b99c6902a389c93` — document source projections do not invent a speaker identity; speaker attribution remains unknown unless the source actually names one.
- `922ccca9dfa0f9bf59c30e79d9a071e3bf6d73bf` — regression coverage asserts the Progressive Understanding observation preserves unknown speaker attribution and source evidence identity.

- `54e01b84ef09866d333d21d8dea770e7dc17cdf8` / `8f224659c64770939f3a939899a6ceb449292746` — the existing situation hydration now includes caregiver-approved document assertions in the Progressive Understanding observation stream and updates its captured timeline.
- `2d85850740d0be375275b7015e55187f48be76a9` / `1fd788b055bcf78d0e8648e57b99c6902a389c93` / `922ccca9dfa0f9bf59c30e79d9a071e3bf6d73bf` — source attribution in that projection remains unknown when no speaker is explicitly identified; tests prohibit invented speaker identity.
- `0c8ca3a201b021586856bac5e2f9738fbda3d6f1` / `34072792925d838599be58b5958d80f42842f5c7` / `fbbb7127d6f4dff6c98d2877cab2a3cea82cb5d2` — projection covers time-bound and reported medication assertions, preserves the existing source-statement contract and avoids misclassifying document roles as speakers.
- `5ec20cae34df9ddbbac64dd7536d5dee4adf89c2` — live conflict evaluation uses neutral document names so filename recency cannot bias the model's conflict explanation.

- `c429ccf754e378d9e0aa03f737877491ae9ea7cd` — the live scanned-PDF/DOCX conflict test also verifies both source-backed assertions reach the existing Progressive Understanding situation response.

- `0dfbd77077c30eb50e6f954d89b257344396057a` — live conflict evaluation verifies unresolved document conflicts are also surfaced through caregiver return-continuity context, not only the internal LCR projection.

- `0ad9f6a6a05fbae17384d920a6a3cf7ff2d0cbf7` / `5b6007f6d1ebd9d5e562e9bcf1a0e76a6313cae0` — live-Qwen evaluation now accepts a patient denial, verifies the persisted negation qualifier, and checks Companion preserves the denial rather than reversing its meaning.
