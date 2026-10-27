# Qwen Companion Grounding and Conversation Quality — Execution Checklist

## Product contract

This work implements the canonical rules in `docs/SOLENOS_MASTER_VALUES.md`: Truth Before Fluency, Evidence Before Interpretation, Provenance Preservation, Explicit Uncertainty, Contextual Understanding, Proportionate Safety, and server-side person/caregiver isolation. A prompt is not proof of enforcement. Each safety-critical behavior needs a code boundary and an executable regression test.

## Work list and acceptance gates

- [x] Read the canonical product values registry and current Companion, gateway, and behavioral-contract tests.
- [x] Keep conversation history as conversational context, never as proof of a care event.
- [x] Retrieve relevant older care events independently from the recent baseline, with server-side person scoping.
- [x] Retrieve relevant older caregiver-owned evidence independently from the most recent documents/evidence.
- [x] Apply the operational-state guard to Companion responses, including sync, reminder delivery, account, access, and lifecycle claims.
- [x] Add a bounded model repair pass for invalid non-greeting responses; fail closed if the repaired response remains unsupported.
- [x] Add regression tests for historical retrieval and an unsupported reminder-delivery claim.
- [ ] Run the full backend test suite in CI and fix every failure introduced by this branch.
- [ ] Run the live Qwen evaluation against the deployed model; mocked tests are not accepted as proof of conversational quality.
- [ ] Evaluate a fixed case set: greetings/thanks, direct care-history question, missing data, older relevant event, contradictory sources, document dates, pronoun/reference resolution, emotional frustration, operational status, medication boundary, emergency language, and cross-person isolation.
- [ ] Record real-model input/context source IDs, output, validator verdict, latency, retry count, and failure category without logging unnecessary sensitive text.
- [ ] Review real responses for directness, conversational naturalness, evidence fidelity, attribution, uncertainty, repetition, and safe next steps.
- [ ] Merge only when CI is green and the live-model gate is honestly reported. A live inference outage is a blocked gate, not a pass.

## Non-negotiable invariants

- Qwen is not the authority for care facts; the caregiver and authorized Living Care Record remain authoritative.
- No Companion response writes care facts to the LCR.
- Conversation messages are not promoted to care evidence.
- A missing record is not proof that an event did not occur.
- Caregiver reports, source documents, interpretations, and unknowns remain distinct.
- No claim of saved/synced/delivered/scheduled/completed/verified status without authoritative backend evidence.
- All retrieval is scoped to the authenticated session's person; evidence retrieval additionally remains caregiver-owned under the current data-access model.
- Do not replace failed inference with a canned response and do not claim real-model quality from mocked outputs.

## Current implementation notes

The Companion now supplements the recent event baseline with lexical matches from older events and supplements recent/linked evidence with matching caregiver-owned evidence. Lexical overlap ranks retrieval candidates only; it does not establish that a claim is true. Response validation checks operational-state claims and allows up to two model repair attempts for non-greeting responses before failing closed.

## Required follow-up

The current retrieval is bounded and lexical. It is an incremental improvement, not a full Care Reality State/delta-engine retrieval architecture. The live Qwen suite and human-reviewed quality evaluation remain mandatory before claiming production conversational quality.
