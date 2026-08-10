-- Source-Pointer Trust Layer — every high-confidence claim must have a verified exact source pointer.
-- Run: psql $DATABASE_URL -f db/migrations/076_source_pointer_trust.sql
--
-- NOTE (honest persistence reality):
-- DARE runtime persistence is currently in-memory. These SQL constraints provide the durable
-- database contract for dare_extraction_candidates / dare_validated_events when/where those
-- tables are actually persisted to Postgres. They are NOT currently the application's active
-- runtime persistence enforcement layer (the deterministic TypeScript validator is).

CREATE EXTENSION IF NOT EXISTS "pgcrypto";

-- ---------------------------------------------------------------------------
-- 1. dare_extraction_candidates — add explicit evidence status + source-pointer provenance
-- ---------------------------------------------------------------------------

ALTER TABLE dare_extraction_candidates
  ADD COLUMN IF NOT EXISTS evidence_status TEXT NOT NULL DEFAULT 'inferred',
  ADD COLUMN IF NOT EXISTS source_span_verified BOOLEAN NOT NULL DEFAULT false,
  ADD COLUMN IF NOT EXISTS source_span_start_offset INTEGER NULL,
  ADD COLUMN IF NOT EXISTS source_span_end_offset INTEGER NULL;

-- The core invariant, enforced at the DB level:
--   confirmed / reported  =>  MUST have a verified exact source pointer
--   inferred/unknown/contradictory  =>  no pointer required
DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint
    WHERE conname = 'dare_extraction_candidates_source_pointer_trust'
  ) THEN
    ALTER TABLE dare_extraction_candidates
      ADD CONSTRAINT dare_extraction_candidates_source_pointer_trust
      CHECK (
        evidence_status IN ('inferred', 'unknown', 'contradictory')
        OR (
          evidence_status IN ('confirmed', 'reported')
          AND source_span IS NOT NULL
          AND source_span <> ''
          AND source_span_verified = true
          AND source_span_start_offset IS NOT NULL
          AND source_span_end_offset IS NOT NULL
          AND source_span_start_offset >= 0
          AND source_span_end_offset > source_span_start_offset
        )
      );
  END IF;
END $$;

COMMENT ON COLUMN dare_extraction_candidates.evidence_status IS
  'Evidentiary status — independent of numeric confidence. confirmed/reported REQUIRE a verified exact source pointer.';
COMMENT ON COLUMN dare_extraction_candidates.source_span_verified IS
  'true only if originalText.slice(start,end) === source_span exactly (no normalization).';

-- ---------------------------------------------------------------------------
-- 2. dare_validated_events — the persisted truth layer must preserve the same invariant
-- ---------------------------------------------------------------------------

ALTER TABLE dare_validated_events
  ADD COLUMN IF NOT EXISTS source_span TEXT NULL,
  ADD COLUMN IF NOT EXISTS evidence_status TEXT NOT NULL DEFAULT 'inferred',
  ADD COLUMN IF NOT EXISTS source_span_verified BOOLEAN NOT NULL DEFAULT false,
  ADD COLUMN IF NOT EXISTS source_span_start_offset INTEGER NULL,
  ADD COLUMN IF NOT EXISTS source_span_end_offset INTEGER NULL;

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint
    WHERE conname = 'dare_validated_events_source_pointer_trust'
  ) THEN
    ALTER TABLE dare_validated_events
      ADD CONSTRAINT dare_validated_events_source_pointer_trust
      CHECK (
        evidence_status IN ('inferred', 'unknown', 'contradictory')
        OR (
          evidence_status IN ('confirmed', 'reported')
          AND source_span IS NOT NULL
          AND source_span <> ''
          AND source_span_verified = true
          AND source_span_start_offset IS NOT NULL
          AND source_span_end_offset IS NOT NULL
          AND source_span_start_offset >= 0
          AND source_span_end_offset > source_span_start_offset
        )
      );
  END IF;
END $$;

-- ---------------------------------------------------------------------------
-- 3. dare_claim_downgrades — structured, queryable downgrade history (extraction-quality metric)
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS dare_claim_downgrades (
  id TEXT PRIMARY KEY,
  claim_id TEXT NOT NULL,
  evidence_id TEXT NULL,
  original_confidence NUMERIC NULL,
  final_confidence NUMERIC NULL,
  original_status TEXT NOT NULL,
  final_status TEXT NOT NULL,
  reason TEXT NOT NULL,
  source_span_before TEXT NULL,
  source_span_start_offset INTEGER NULL,
  source_span_end_offset INTEGER NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS dare_claim_downgrades_claim_id_idx
  ON dare_claim_downgrades (claim_id);
CREATE INDEX IF NOT EXISTS dare_claim_downgrades_reason_idx
  ON dare_claim_downgrades (reason);
CREATE INDEX IF NOT EXISTS dare_claim_downgrades_created_at_idx
  ON dare_claim_downgrades (created_at);

COMMENT ON TABLE dare_claim_downgrades IS
  'Records every automatic evidence-status downgrade so extraction quality can be measured.';
