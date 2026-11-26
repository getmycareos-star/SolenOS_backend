# Qwen inference-path inventory

Reviewed against backend `main` on 2026-11-24. This is a source-level inventory, not proof that a live model is available or that every response is reliable.

## Single transport and enforcement boundary

- `backend/app/services/qwen_gateway.py::qwen_chat` is the only native Ollama `/api/chat` transport.
- `qwen_chat` performs workflow-specialist selection, then applies the shared SolenOS system contract before context-budget preflight and network inference.
- `extract_review_candidates` and `extract_companion_care_candidates` are gateway-owned extraction entry points; both delegate model inference to `qwen_chat`.
- No `/api/generate` callsite was found in the repository search. The static audit rejects native Ollama transport configuration or `/api/chat` usage outside the gateway.

## Direct `qwen_chat` callsites

| Source | Entry points / usage | Execution mode |
| --- | --- | --- |
| `backend/app/api/collaboration.py` | `qwen_support_assist`; `companion_message` via bounded-context gateway calls; legacy `_ollama_chat` adapter; claim/care-understanding extraction; production evaluation | Synchronous request handlers and `asyncio.to_thread` for bounded Companion work; every active path enters `qwen_chat` |
| `backend/app/api/daily_care_intelligence.py` | Daily Care answer generation and schema-repair retry | Async handler offloads synchronous gateway call with `asyncio.to_thread` |
| `backend/app/api/handoff_drafts.py` | Grounded handoff generation and bounded repair retry | Synchronous request path |
| `backend/app/services/delta_engine.py` | Delta JSON generation and retry | Synchronous service path |
### Delta Engine activation boundary

The `/care-delta/reconcile` API currently calls `reconcile_event` without `model_enrichment=True`; its production default is the deterministic change-intelligence path and does not invoke Qwen. The optional Qwen Delta enrichment path is entered only by an internal caller that explicitly enables `model_enrichment=True`. When enabled, it uses the same `qwen_gateway.qwen_chat` transport and the `delta_engine` specialist workflow contract.

The live Delta acceptance workflow now invokes that opt-in service path directly, verifies the configured local Ollama model and persisted `CareDeltaRun`, and asserts that source-linked assertions are not marked accepted or verified without caregiver review. The deterministic API path remains separately covered by API tests. This distinction prevents the inference inventory from overstating which production requests currently invoke Qwen.
| `backend/app/services/qwen_background_worker.py` | Workflow classification; asynchronous background job processing | Background worker uses `asyncio.to_thread`; worker loop is started by application lifespan when enabled |

## Disabled / excluded source paths

- `backend/app/api/collaboration.py::community_experience` still contains a legacy `qwen_chat` call in source, but Community is intentionally disabled: application middleware returns 404 for `/api/v1/community` before route execution and the Community router is not an active registered route. This is not an active inference path. Keep it excluded; do not re-enable it as part of Qwen integration.
- No direct Ollama client (`ollama.Client`, `ollama.chat`) or alternate `/api/generate` transport was found in backend application source. Native transport is confined to the gateway.

## Gateway extraction consumers

| Source | Gateway entry point | Purpose |
| --- | --- | --- |
| `backend/app/api/v1.py` | `extract_review_candidates` | Daily capture / source-grounded review candidates |
| `backend/app/api/document_intelligence.py` | `extract_review_candidates` | Persisted document extraction candidates; review-only until caregiver acceptance |
| `backend/app/services/qwen_background_worker.py` | `extract_review_candidates` | Background capture/document extraction |
| `backend/app/services/companion_care_updates.py` | `extract_companion_care_candidates` | Candidate extraction from Companion messages; proposal/review flow, not automatic LCR authority |

## Routing and authority rules

- Workflow IDs come from backend-owned system messages. Unknown or ambiguous IDs fail closed; user text and uploaded documents cannot select specialists.
- Enabled workflows are accounted for by `WORKFLOW_SPECIALISTS` or `WORKFLOWS_WITHOUT_SPECIALISTS` in `qwen_specialized_prompts.py`.
- Specialist prompts define task responsibilities only. The shared evidence/safety/authority enforcement, personality constitution, and product identity are reasserted after task instructions.
- Background classification is a meta-task and deliberately receives no care specialist.
- Extraction and generation remain proposals. Backend review, authorization, reconciliation, and persistence own all authoritative LCR transitions.

## Routing and context-budget implementation details

- The current runtime specialist cap is **two** specialists per request, not four. The cap is enforced by `MAX_SPECIALISTS_PER_REQUEST`; the enabled-workflow map currently assigns no workflow more than two.
- Workflow routing is checked at module initialization: every enabled classifier workflow must have exactly one routing decision; specialist-free workflows must be enabled; internal-only API workflow IDs may exist in the specialist map without being classifier options. Overlap, undefined specialists, empty mappings, and over-cap mappings fail application initialization.
- Repeated explicit workflow markers are rejected as ambiguous, even when the repeated ID is identical. Unknown explicit IDs fail closed.
- `_estimate_prompt_tokens` is the gateway's preflight estimator. It is a conservative UTF-8 byte heuristic (UTF-8 bytes divided by 3, plus fixed message-framing allowance), **not a model-specific tokenizer**. It must not be reported as an exact Qwen token count. Budget tests exercise this exact estimator; do not report its output as an exact Qwen token count.

## Durable invocation audit

The shared gateway now uses the existing PostgreSQL-backed `ai_inference_metrics` governance table for a two-stage invocation receipt:

- Before sending a request to Ollama, the gateway commits an `outcome=started` record. If this receipt cannot be persisted, the model request is not sent.
- After the upstream response or transport failure, the gateway finalizes that same record with success/failure, latency, available token counts, timeout/error classification, and Ollama completion metadata.
- The record includes the workflow identifier, operating-contract version, system-prompt SHA-256, and request-context SHA-256. It does not persist prompt text, source text, or model response content.
- If the successful model response cannot be durably finalized, the gateway withholds the answer and returns `inference_audit_unavailable`. A process termination after the start receipt leaves a durable `started` record for operational reconciliation.
- Context-budget and configuration rejections are recorded as failed preflight outcomes; they are not represented as completed model invocations.

Trusted `audit_context` is now propagated by the identified production workflow families: Companion response, Companion care-update extraction, Daily Care extraction, document candidate extraction, care-claim extraction, care understanding, handoff drafting (including bounded repair inference), and Delta reconciliation (including its bounded retry). These contexts carry the authenticated person/caregiver scope where available, request/session correlation IDs, and the exact source event/evidence/document/appointment/assertion/LCR references selected by the backend. The metric's `person_id` foreign key and bounded metadata-only source-reference lists connect each invocation to its authorized retrieval set. Raw source text, user text, and model output remain excluded. Test-only bounded-context helpers and any future Qwen call site must preserve this same rule; the static gateway audit test and call-site review are the guard against adding an untracked invocation.

## Output-validation audit

Companion now records the final response-grounding decision in the existing AI governance tables:

- A passed response stages its `AISafetyCheck` row in the same SQLAlchemy transaction as the user and assistant conversation turns; the audit and answer commit or roll back together.
- A rejected response creates both a rejected `AISafetyCheck` and an `AIOutputRejection` record before returning the safe failure response. No rejected assistant turn is persisted.
- Audit details include the authenticated person/caregiver scope, request correlation, workflow, model/contract identity when generated by Qwen, response SHA-256, rejection reason codes, and the authorized source-reference IDs.
- Raw model response text is never copied into these audit records.
- Deterministic grounding fallbacks are explicitly labelled as deterministic rather than attributed to Qwen.

Handoff drafts and Delta reconciliation retain their existing source snapshots, validation outcomes, and persisted run/draft records. The PostgreSQL integration suite now exercises both invocation-receipt persistence and Companion output-validation audit persistence.

## Verification boundary

The AST contract test inventories direct `qwen_chat` imports/callback references, rejects unapproved aliases and native transport bypasses, and checks gateway wrapper delegation. It does not prove live Ollama reachability, real-model behaviour, worker scheduling under production load, or the absence of inference hidden behind future non-Python integrations. Those require live inference and runtime evidence.

## Remaining Phase 2 evidence

- Run the targeted static audit and backend test suite on this exact commit.
- Run live Qwen evaluations for every enabled workflow and inspect actual request routing.
- Exercise the background worker loop with PostgreSQL-backed jobs and confirm safe retry/failure behaviour.
- Confirm deployed runtime revision separately; no deployment is performed by this inventory.

## Direct-main Companion claim grounding and extraction hardening — 2026-11-26

**Status: implemented on backend main; exact-current-main CI and live-model verification pending.**

- The ordinary Companion conversation route now builds claim provenance against persisted, person-scoped source records before staging a passed response-grounding audit. Unmatched material care claims are rejected; the rejection audit is persisted without a passed audit or conversation turn.
- The source-record scope derived from authenticated person-scoped queries is explicitly attached before provenance matching. Existing explicit mismatched person IDs remain mismatched and are rejected.
- Companion success audits include handoff source IDs and commit with the corresponding user/assistant turn transaction. The read-only `/assistant/assist` path commits its output-validation receipt before returning because it has no conversation-turn transaction.
- Claim extraction now returns only verbatim claims whose `claim_text` exactly equals a validated, exact-substring `source_span`. Missing, invalid, or mismatched spans are dropped; Qwen cannot emit the `confirmed` tag. The two independent passes remain review-only and do not write to the LCR.
- Care-understanding proposals now validate every section's field types, lengths, and enumerations. Extractive descriptions, event/decision participants, reasons, outcome text, times, and non-care facts must be source-bound; clarification questions remain review proposals tied to an exact raw fragment. Possible links must quote source wording and cannot use causal wording while labelling causation false.
- Background Qwen output persistence now requires both confirmed live inference and `grounding_status=backend_validation_passed`. Persisted output metadata includes assertion IDs, LCR context IDs, retrieval coverage, response origin/mode, and existing source IDs. Outputs remain pending human review and `written_to_lcr=false`.
- Frontend claim source-pointer verification now uses Unicode code-point offsets to match Python backend indexing, rejects fractional and out-of-range offsets, and has regression coverage for emoji preceding the cited span.
- Regression tests cover unsupported paraphrases, absence of false passed audits, missing/mismatched spans, overclaimed confirmation, malformed care-understanding structures, causal-link mislabelling, complete worker provenance, and Unicode offsets.

**Verification boundary:** these changes are implemented in source and tests, but the exact current backend/frontend Actions runs have not completed successfully in the observed session. Live Qwen acceptance is not yet established. No deployment was performed.
