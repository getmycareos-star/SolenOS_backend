# SolenOS — Source-Pointer Trust Layer (DARE enrichment)

## Status
COMPLETE. Verified via 32 passing tests (10 unit + 5 realistic caregiver inputs + invariant checks) and a successful production build. Migration file is `076_source_pointer_trust.sql` (not 077).

## Current Architecture
- **Extraction pipeline = DARE** (`src/lib/data-acquisition-resilience/`):
  - `RawInput` — raw evidence (`content`, `id`, `caregiver_id`, `input_type`, ...)
  - `ExtractionCandidate` — a claim: `extracted_fact`, `event_signal`, `confidence` (0–1 numeric), `confidence_sources`, `source_span`, `extraction_method`, `ambiguity_flags`, `completeness`, `created_at`
  - `ValidatedCareEvent` — the persisted truth layer (mapped from candidates via normalization)
  - `pipeline.ts` orchestrates: `storeRawInput → checkOcrFailure → extractCandidatesFromRawInput → applyRepeatedSignalBoost → storeCandidates → normalizeEvents → buildUncertainEvents → reconcileCrossDocument → validated_events`
- **Persistence is IN-MEMORY** today (`projection-store.ts`, `raw-input-store.ts`). The SQL migrations define `dare_raw_inputs`, `dare_extraction_candidates`, `dare_validated_events`, but the current application has NO live Postgres write path for these DARE objects.
- **Confidence semantics**: `confidence` is a 0–1 numeric; `AUTO_VALIDATE_CONFIDENCE = 0.65` controls numeric validation. There is NO existing `evidence_status` field.
- **Self-consistency**: `applyRepeatedSignalBoost` (boosts numeric confidence on repeated signals), `reconcileCrossDocument` (conflict detection), `buildUncertainEvents` (quarantine low-confidence).
- **`source_span`**: currently the extracted text span itself, with NO offsets and NO verification against the original evidence.

## Known Limitation (must stay documented)
> «DARE runtime persistence is currently in-memory. SQL constraints provide the durable database contract but are not currently the application's active persistence enforcement layer.»

Therefore:
- Runtime protection = deterministic application-level enforcement (implemented now).
- Database protection = SQL constraints on the DARE tables (durable contract for when/where those tables are persisted to Postgres).
- We do NOT represent the DB constraint as live runtime protection today.

## New Invariants
Two independent dimensions are preserved (never collapsed):
- `confidence: number` — model/extraction confidence (0–1).
- `evidence_status: "confirmed" | "reported" | "inferred" | "unknown" | "contradictory"` — the evidentiary status.

Other distinct dimensions: `source_pointer`, `consistency`, `human_confirmation`.

### Source-pointer invariant
For any claim with `evidence_status` = `confirmed` OR `reported`, ALL of the following are mandatory:
1. `source_span` exists and is non-empty
2. `source_span_start_offset` exists and `>= 0`
3. `source_span_end_offset` exists and `> start_offset`
4. `end_offset <= originalText.length`
5. Exact verification: `originalText.slice(start_offset, end_offset) === source_span`
   - NO normalization, trimming, paraphrasing, case conversion, whitespace substitution, or fuzzy matching.

If ANY requirement fails → `evidence_status = "unknown"`, preserve the claim, record the downgrade + reason, and do NOT allow a later confidence boost to restore the previous status.

### Confidence / Status separation
- `AUTO_VALIDATE_CONFIDENCE` (0.65) may continue to control existing numeric validation behavior, but it is NOT an evidentiary-status threshold.
- `confidence >= X` must NEVER be treated as `confirmed`.
- `applyRepeatedSignalBoost` may increase numeric `confidence` but MUST NOT upgrade `evidence_status` (e.g. `inferred → confirmed`).
- `confirmed` is only established by the explicit evidence/confirmation rules of the trust architecture.

### Downgrade rules (conservative by construction)
- Validation may only downgrade `evidence_status` / keep it. It must NEVER upgrade.
- `confirmed` + valid pointer → preserved
- `reported` + valid pointer → preserved
- `confirmed`/`reported` + invalid or missing pointer → `unknown`
- `inferred`/`unknown`/`contradictory` (+ no pointer) → allowed
- No silent upgrades anywhere.

## Required Pipeline Order
```
storeRawInput
→ checkOcrFailure
→ extractCandidatesFromRawInput
→ verifySourcePointer
→ enforceSourcePointer
→ applyRepeatedSignalBoost
→ storeCandidates
→ normalizeEvents
→ buildUncertainEvents
→ reconcileCrossDocument
→ validated_events
```
Protection: a candidate that fails source-pointer verification is marked `evidence_status = "unknown"` and `source_span_verified = false` BEFORE `applyRepeatedSignalBoost`. `applyRepeatedSignalBoost` is gated to NOT boost (numeric or status) any claim with `source_span_verified = false`. Downstream `normalizeEvents`/`reconcileCrossDocument` are not allowed to manufacture `confirmed`.

## Files To Change
**Created:**
1. `db/migrations/076_source_pointer_trust.sql` — add columns + CHECK constraints to `dare_extraction_candidates` and `dare_validated_events`; create `dare_claim_downgrades` table.
2. `src/lib/data-acquisition-resilience/source-pointer.ts` — deterministic `verifySourcePointer` + `enforceSourcePointer` (+ derived helpers).
3. `src/lib/data-acquisition-resilience/source-pointer-store.ts` — in-memory downgrade log (+ documented Postgres hook).
4. `src/lib/data-acquisition-resilience/trust-layer.test.ts` — deterministic unit + pipeline + realistic caregiver-input tests (32 checks total). Run via `npm run test:trust`.

**Edit:**
5. `src/lib/data-acquisition-resilience/types.ts` — add `EvidenceStatus` type; add `evidence_status`, `source_span_verified`, `source_span_start_offset`, `source_span_end_offset` to `ExtractionCandidate` and `ValidatedCareEvent`; add downgrade-log types.
6. `src/lib/data-acquisition-resilience/extract-candidates.ts` — set initial `evidence_status` on candidates deterministically.
7. `src/lib/data-acquisition-resilience/pipeline.ts` — insert `verify/enforce` in the mandated order; gate `applyRepeatedSignalBoost`; block invalid-pointer promotion to validated events; write downgrade log.
8. `src/lib/data-acquisition-resilience/index.ts` — export new functions/types.

## Test Plan
### Source pointer (pointer correctness)
1. Exact valid span
2. Wrong source text
3. Wrong start offset
4. Wrong end offset
5. Empty source span
6. Negative offset
7. `end <= start`
8. Span outside original text
9. Unicode/multibyte text
10. Repeated identical phrases — offsets must identify the intended occurrence

### Evidence status
- `confirmed` + valid pointer → preserved
- `reported` + valid pointer → preserved
- `confirmed` + invalid pointer → `unknown`
- `reported` + invalid pointer → `unknown`
- `inferred` + no pointer → allowed
- `unknown` + no pointer → allowed
- `contradictory` + no pointer → allowed

### Explicit confidence/status separation
- `confidence` increase → MUST NOT upgrade `evidence_status`
- repeated signal → MUST NOT manufacture `confirmed`

### Pipeline
- invalid pointer → downgrade → repeated-signal boost → normalization → reconciliation cannot result in `confirmed`.

### Realistic inputs (5)
1. Medication change
2. Hospital discharge
3. Symptom/behavior observation
4. Appointment information
5. Contradictory information

### Database
- If `DATABASE_URL` is available: run the DB constraint test against that Postgres DB.
- If not: run all deterministic tests locally; keep the Postgres constraint test isolated so it can run when a valid test DB is provided. Do NOT claim the SQL constraint was integration-tested against real Postgres unless it actually was.

## Scope Control
This task implements ONLY the source-pointer trust layer integrated into DARE. No change detection, triage, follow-up detection, relationship graph, Care Passport, or unrelated features.
