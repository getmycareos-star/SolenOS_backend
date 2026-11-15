# SolenOS retrieval reliability — main-branch audit

Date: 2026-11-15
Scope: canonical `main` branches only. No pull-request branches were used.
Backend HEAD at audit: `f7bb394b9255d9700e0c5406e2136307a1e9210f`.
Frontend HEAD at audit: `c9e31f1f8a3351aa70676e5d54a6c35f58dce99c`.

This is an implementation map and gap register, not a production-readiness or clinical-validation claim.

## Operating decision

All work for this programme is committed directly to the two canonical `main` branches. Existing open PRs are intentionally left untouched for later review. Preserve the current canonical LCR, PUE, Delta Engine, evidence/provenance and reconciliation services. Do not create a parallel record or intelligence engine.

## Verified data-flow map

| Path | Current frontend entry | Backend owner / route | Persisted owner | Retrieval / downstream | Status |
|---|---|---|---|---|---|
| Daily Care → LCR | Daily Care workspace; quick and structured capture; `CareDeltaPanel` | `POST /api/v1/daily/log`; `_save_daily_capture_event`; `POST /api/v1/care-delta/reconcile` | `CareEvent`, linked `Evidence`, persisted `CareDeltaRun`, assertions and reconciliation records | `GET /api/v1/care-continuity/current-care`; `GET /api/v1/care-continuity/context`; saved Delta run reads | Implemented on main; exact PostgreSQL, restart and live-path acceptance still required |
| Documents → LCR | Documents workspace; original upload and document review UI | `POST /api/v1/upload`; document-intake worker; `document_lifecycle.py`; `document_intelligence.py` | Original `Evidence` / upload, `CaptureAnalysis` extraction and review history, governed proposals/assertions where explicitly reviewed | LCR context includes available `document_extractions` as `document_extracted_unreviewed`; evidence and document-analysis routes | Intake/extraction/review infrastructure exists; full source-to-accepted-LCR lifecycle and PostgreSQL proof remain required |
| AI Companion → LCR | `AICompanion.tsx` sends conversation messages | `POST /api/v1/conversation/message`; `companion_care_updates.py` helper; mounted `companion_review.py` review router | `ConversationSession` / `ConversationMessage`; optional `AIOutputLCRProposal`; accepted `CareAssertion` only after caregiver review | Companion care-update review endpoints exist, but current frontend contract explicitly forbids proposal generation/review and tells caregivers Companion messages are not added to the care record | **Not connected end-to-end in the active caregiver UI.** This is a known product-path gap, not a missing second engine |
| LCR → Companion | Companion conversation composer | `collaboration.py:companion_message`; `unified_lcr_retrieval.py:build_unified_lcr_context`; `lcr_context.py:build_lcr_context` | Canonical source tables; prior dialogue remains separate conversational context | `GET /api/v1/care-continuity/context` and Companion prompt context | Implemented with explicit coverage metadata; relevance, prompt-fit and long-history behavior need adversarial verification |
| LCR → family/professional handoff | Companion handoff workflow | `handoff_drafts.py` and handoff delivery routes | `HandoffDraft`, immutable revisions/source snapshots and delivery records | Draft read/revision/approval, sharing and acknowledgement routes | Workflow infrastructure exists; each output claim and completeness boundary needs verification |
| LCR → appointment preparation | Appointment workspace | `appointment_workspace.py` | Appointment workspace records/revisions, proposals and linked evidence | Appointment workspace and preparation/source-context reads | Workflow infrastructure exists; source completeness and stale-context handling need end-to-end verification |

## Canonical architecture owners

- Raw Daily Care event and evidence persistence: `backend/app/api/v1.py`.
- Canonical LCR read projection: `backend/app/services/lcr_context.py`.
- Unified Companion retrieval composition: `backend/app/services/unified_lcr_retrieval.py`.
- Durable change reconciliation: `backend/app/services/change_intelligence_reconciliation.py`, invoked through existing capture, Delta and care-continuity paths.
- Persisted Delta comparison: `backend/app/services/delta_engine.py`.
- Document lifecycle and extraction: `backend/app/api/document_lifecycle.py`, `backend/app/api/document_intelligence.py`, and `backend/app/services/qwen_background_worker.py`.
- Companion disclosure proposal persistence: `backend/app/services/companion_care_updates.py`; caregiver decision boundary: `backend/app/api/companion_review.py`.
- Family handoff preparation: `backend/app/api/handoff_drafts.py`.
- Appointment preparation: `backend/app/api/appointment_workspace.py`.

## Retrieval contract observed on main

- `lcr_context.py` projects person-scoped canonical sources and marks missing required/optional schemas.
- Query and explicit temporal retrieval scan matching rows and rank a bounded page per source; ordinary current-care reads remain recent/bounded.
- Per-source pagination is represented by offsets and `has_more`; callers must not interpret an unreturned page as absence.
- Explicit temporal filtering considers both event/observation time and recorded/ingestion time where the source schema provides them.
- Document extraction is explicitly labelled as unreviewed source material, not an accepted care fact.
- Evidence lineage is based on explicit source/parent references and reviewed relationships; text similarity is not proof of independent corroboration.
- Prior Companion dialogue is a separate context source and is explicitly not care evidence.
- Prompt serialization is bounded and can omit retrieved records; omission must remain visible to the response guard and caregiver-facing coverage explanation.
- Person/caregiver scope is derived from the authenticated server-side session at the API boundary.

## Verified gaps and implementation risks

1. **Companion disclosure capture is not active in the caregiver UI.** Backend proposal persistence and caregiver-review routes exist, but the current frontend verification contract explicitly bans those routes and UI. Phase 2.3 must connect them deliberately, with source quotation, classification, caregiver confirmation/review, idempotency, identity scope and no AI-authored fact promotion.
2. **Documents are not automatically authoritative.** Original files, extraction text, extracted observations, reviewed claims and accepted assertions are distinct. The pipeline must preserve these distinctions and trace each accepted assertion to exact source evidence.
3. **A retrieval scan is not the same as prompt inclusion.** The backend may find and rank more records than fit into the model prompt. The response must disclose omitted source pages and prompt truncation; it must never claim that an unseen record does not exist.
4. **Completeness is source-dependent.** Missing optional tables, inaccessible caregiver-scoped document extraction, failed extraction and unavailable source records must remain visible as incomplete/unknown rather than empty history.
5. **Current-care projection and historical query are different contracts.** A bounded current-care panel must not be mistaken for exhaustive history. Historical requests must use explicit query/temporal paging.
6. **Handoffs and appointment workspaces are persisted snapshots.** Their source references, freshness and output claims require explicit verification; a saved draft is not automatically current or clinically validated.
7. **Synthetic benchmark coverage is not production proof.** Required acceptance includes real PostgreSQL, authenticated API ingestion, fresh-session/restart retrieval, person isolation, per-case source support and zero skipped cases on the exact tested commit.

## First implementation sequence on main

1. Complete and run the existing Daily Care → persisted Delta → canonical LCR tests on exact main SHAs; repair verified defects at the owning service.
2. Exercise document original-upload → extraction → candidate review → caregiver decision → assertion/reconciliation → fresh-session LCR retrieval in PostgreSQL.
3. Connect the existing Companion proposal/review boundary to the active Companion UI; keep ordinary conversation separate from evidence until an explicit caregiver-reviewed action accepts it.
4. Harden unified retrieval and response coverage so scan completeness, page completeness, prompt inclusion and source availability are distinct.
5. Verify answers, explanations, follow-up questions, handoffs and appointment preparation against the same authorized, source-grounded context.
6. Run the messy-care benchmark and production-like PostgreSQL lifecycle tests; report exact commit SHA, workflow URL, per-case results and skips.

## Test and deployment status

This audit does not claim that tests, PostgreSQL lifecycle benchmarks, frontend builds, Railway deployment, Netlify deployment or live caregiver workflows have passed. Those results must be captured from actual runs on the exact commits being assessed.

## Non-negotiable safeguards

- No parallel LCR, PUE or Delta Engine.
- No AI interpretation promoted to a verified fact without the required caregiver review.
- Conversation history is not independent care evidence.
- Preserve exact source text, reporter attribution, event time, recorded time, uncertainty, corrections and contradiction history.
- No cross-person or unauthorized cross-caregiver retrieval.
- No claim of completeness when source pages or prompt context were omitted.
- No production-readiness or clinical-accuracy claims from static checks or synthetic tests alone.
