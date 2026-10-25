# SolenOS local Qwen runtime

## Runtime boundary

- SolenOS uses the open-weight `qwen2.5:1.5b` model through Ollama's native HTTP API.
- The production image bundles Ollama and the model weights. The runtime runs beside FastAPI inside the Railway backend container.
- No hosted Qwen/OpenAI/Gemini inference API, paid model API, local Dell process, or browser inference is used.
- The application fails closed if the model is missing or unavailable; it does not substitute canned conversational replies.
- Railway hosting/build resource use still follows the existing Railway plan; no separate Qwen API subscription is needed.

## Qwen's role

Qwen is a support layer, not the authority. SolenOS's care-record data, caregiver-confirmed facts, source provenance, access controls and explicit review decisions remain authoritative.

Qwen may:
- Generate natural language for the Companion, including brief, respectful greetings.
- Explain authorized care context and identify unknowns.
- Help organize documents, appointment questions, after-appointment follow-up, caregiver handoffs, professional-summary drafts, open loops, two-week reviews, decision/outcome notes and medication observations.
- Summarize only published peer experiences in Community.
- Propose source-grounded observation candidates with exact source quotes.

Qwen may not diagnose, prescribe, infer causation from patterns, fabricate source quotes, promote its suggestions into the Living Care Record, or let Community posts modify the LCR. Emergency escalation is handled by the explicit safety path, not by model inference.

## Persistence and review

Care capture persists first and returns the saved event/care-record projection. Qwen extraction is a separate request using the returned server event ID. Candidate rows are persisted separately, bound to the authorized care record and source event, and require explicit caregiver review. Accepting or rejecting a candidate records the reviewer and decision but does not itself create or modify an authoritative LCR fact.

The Alembic revision 20261024_11 is a compatibility bridge: older production databases already stamped at that revision had caregiver-consent and notification tables, while fresh installs now reach it from the confidence-cleanup migration. The migration is idempotent and ensures those tables exist on fresh installs. Revision 20261024_12 creates Qwen review-candidate storage and repeats confidence cleanup for databases that had already stamped the older revision 11.
