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
| `backend/app/api/collaboration.py` | `_ollama_chat`; Companion inference and bounded-context requests; claim/care-understanding extraction; production evaluation | Synchronous request handlers and `asyncio.to_thread` for bounded Companion work |
| `backend/app/api/daily_care_intelligence.py` | Daily Care answer generation and schema-repair retry | Async handler offloads synchronous gateway call with `asyncio.to_thread` |
| `backend/app/api/handoff_drafts.py` | Grounded handoff generation and bounded repair retry | Synchronous request path |
| `backend/app/services/delta_engine.py` | Delta JSON generation and retry | Synchronous service path |
| `backend/app/services/qwen_background_worker.py` | Workflow classification; asynchronous background job processing | Background worker uses `asyncio.to_thread`; worker loop is started by application lifespan when enabled |

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

## Verification boundary

The AST contract test inventories direct `qwen_chat` imports/callback references, rejects unapproved aliases and native transport bypasses, and checks gateway wrapper delegation. It does not prove live Ollama reachability, real-model behaviour, worker scheduling under production load, or the absence of inference hidden behind future non-Python integrations. Those require live inference and runtime evidence.

## Remaining Phase 2 evidence

- Run the targeted static audit and backend test suite on this exact commit.
- Run live Qwen evaluations for every enabled workflow and inspect actual request routing.
- Exercise the background worker loop with PostgreSQL-backed jobs and confirm safe retry/failure behaviour.
- Confirm deployed runtime revision separately; no deployment is performed by this inventory.