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
- `_estimate_prompt_tokens` is the gateway's preflight estimator. It is a conservative UTF-8 byte heuristic (approximately 3.5 bytes/token plus message framing), **not a model-specific tokenizer**. Budget tests exercise this exact estimator; do not report its output as an exact Qwen token count.

## Verification boundary

The AST contract test inventories direct `qwen_chat` imports/callback references, rejects unapproved aliases and native transport bypasses, and checks gateway wrapper delegation. It does not prove live Ollama reachability, real-model behaviour, worker scheduling under production load, or the absence of inference hidden behind future non-Python integrations. Those require live inference and runtime evidence.

## Remaining Phase 2 evidence

- Run the targeted static audit and backend test suite on this exact commit.
- Run live Qwen evaluations for every enabled workflow and inspect actual request routing.
- Exercise the background worker loop with PostgreSQL-backed jobs and confirm safe retry/failure behaviour.
- Confirm deployed runtime revision separately; no deployment is performed by this inventory.