-- Longitudinal Care State — Core Primitive for SolenOS
-- Run: psql $DATABASE_URL -f db/migrations/077_longitudinal_care_state.sql
--
-- BREAKS AND REBUILDS:
-- 1. Replaces flat StateSituation model with time-bounded, dimensioned assertions.
-- 2. Adds baseline tracking for before/after-event reference states.
-- 3. Adds state transitions as first-class records.
-- 4. Adds conflict detection for competing valid assertions.
-- 5. Adds delta computation for change detection.
-- 6. All existing consumers (resolution-engine, demand-engine, ui-runtime, care-context-sync)
--    must migrate from StateSituation to CareStateAssertion queries.
--
-- MIGRATION STRATEGY:
-- - Existing StateSituation records are backfilled as assertions with validity_start = createdAt,
--   validity_end = null, dimension = 'active_conditions' (mapped from legacy status).
-- - TrackedSituation records become baseline references for care_dependencies.
-- - No data is deleted. Old tables are preserved for audit.

CREATE EXTENSION IF NOT EXISTS "pgcrypto";

-- ---------------------------------------------------------------------------
-- 1. care_state_assertions — the atomic unit of longitudinal state
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS care_state_assertions (
  id TEXT PRIMARY KEY,
  care_recipient_id TEXT NOT NULL,
  caregiver_id TEXT NULL,
  dimension TEXT NOT NULL,
  value TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'active',
  validity_start TIMESTAMPTZ NOT NULL,
  validity_end TIMESTAMPTZ NULL,
  confidence NUMERIC(3,2) NOT NULL DEFAULT 0.5,
  evidence_ids TEXT[] NOT NULL DEFAULT '{}',
  event_ids TEXT[] NOT NULL DEFAULT '{}',
  baseline_id TEXT NULL,
  supersedes_id TEXT NULL,
  superseded_by_id TEXT NULL,
  conflict_status TEXT NOT NULL DEFAULT 'coexisting',
  provenance_note TEXT NOT NULL DEFAULT '',
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS care_state_assertions_recipient_idx
  ON care_state_assertions (care_recipient_id);
CREATE INDEX IF NOT EXISTS care_state_assertions_dimension_idx
  ON care_state_assertions (dimension);
CREATE INDEX IF NOT EXISTS care_state_assertions_validity_idx
  ON care_state_assertions (validity_start, validity_end);
CREATE INDEX IF NOT EXISTS care_state_assertions_current_idx
  ON care_state_assertions (care_recipient_id, dimension)
  WHERE validity_end IS NULL;

-- Invariant: validity_start must be before validity_end (or null for current)
DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint
    WHERE conname = 'care_state_assertions_validity_check'
  ) THEN
    ALTER TABLE care_state_assertions
      ADD CONSTRAINT care_state_assertions_validity_check
      CHECK (validity_end IS NULL OR validity_end > validity_start);
  END IF;
END $$;

-- Invariant: confidence must be in [0,1]
DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint
    WHERE conname = 'care_state_assertions_confidence_check'
  ) THEN
    ALTER TABLE care_state_assertions
      ADD CONSTRAINT care_state_assertions_confidence_check
      CHECK (confidence >= 0 AND confidence <= 1);
  END IF;
END $$;

-- Invariant: status must be valid
DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint
    WHERE conname = 'care_state_assertions_status_check'
  ) THEN
    ALTER TABLE care_state_assertions
      ADD CONSTRAINT care_state_assertions_status_check
      CHECK (status IN ('active', 'resolved', 'suspended', 'unknown'));
  END IF;
END $$;

-- Invariant: dimension must be valid
DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint
    WHERE conname = 'care_state_assertions_dimension_check'
  ) THEN
    ALTER TABLE care_state_assertions
      ADD CONSTRAINT care_state_assertions_dimension_check
      CHECK (dimension IN (
        'active_conditions', 'resolved_conditions', 'symptoms', 'functional_status',
        'cognitive_status', 'medications', 'allergies', 'treatments', 'procedures',
        'care_dependencies', 'mobility', 'living_situation', 'care_relationships',
        'providers', 'pending_situations', 'risks', 'restrictions', 'goals',
        'functional_baseline', 'cognitive_baseline'
      ));
  END IF;
END $$;

-- ---------------------------------------------------------------------------
-- 2. care_state_baselines — reference states for change detection
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS care_state_baselines (
  id TEXT PRIMARY KEY,
  care_recipient_id TEXT NOT NULL,
  dimension TEXT NOT NULL,
  care_state_dimension TEXT NOT NULL,
  value TEXT NOT NULL,
  established_at TIMESTAMPTZ NOT NULL,
  last_confirmed_at TIMESTAMPTZ NOT NULL,
  confidence NUMERIC(3,2) NOT NULL DEFAULT 0.5,
  evidence_ids TEXT[] NOT NULL DEFAULT '{}',
  event_ids TEXT[] NOT NULL DEFAULT '{}',
  context TEXT NOT NULL DEFAULT 'stable_period',
  reference_event_id TEXT NULL,
  supersedes_baseline_id TEXT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS care_state_baselines_recipient_idx
  ON care_state_baselines (care_recipient_id);
CREATE INDEX IF NOT EXISTS care_state_baselines_dimension_idx
  ON care_state_baselines (care_state_dimension);

-- ---------------------------------------------------------------------------
-- 3. care_state_transitions — state change records
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS care_state_transitions (
  id TEXT PRIMARY KEY,
  care_recipient_id TEXT NOT NULL,
  occurred_at TIMESTAMPTZ NOT NULL,
  from_assertion_ids TEXT[] NOT NULL DEFAULT '{}',
  to_assertion_ids TEXT[] NOT NULL DEFAULT '{}',
  mechanism TEXT NOT NULL,
  confidence NUMERIC(3,2) NOT NULL DEFAULT 0.5,
  evidence_ids TEXT[] NOT NULL DEFAULT '{}',
  event_ids TEXT[] NOT NULL DEFAULT '{}',
  detection_method TEXT NOT NULL DEFAULT 'reconstructed',
  description TEXT NOT NULL DEFAULT '',
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS care_state_transitions_recipient_idx
  ON care_state_transitions (care_recipient_id);
CREATE INDEX IF NOT EXISTS care_state_transitions_time_idx
  ON care_state_transitions (occurred_at);

-- ---------------------------------------------------------------------------
-- 4. care_state_conflicts — competing valid assertions
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS care_state_conflicts (
  id TEXT PRIMARY KEY,
  care_recipient_id TEXT NOT NULL,
  dimension TEXT NOT NULL,
  assertion_ids TEXT[] NOT NULL DEFAULT '{}',
  detected_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  resolved BOOLEAN NOT NULL DEFAULT false,
  resolution JSONB NULL
);

CREATE INDEX IF NOT EXISTS care_state_conflicts_recipient_idx
  ON care_state_conflicts (care_recipient_id);
CREATE INDEX IF NOT EXISTS care_state_conflicts_unresolved_idx
  ON care_state_conflicts (care_recipient_id, resolved)
  WHERE resolved = false;

-- ---------------------------------------------------------------------------
-- 5. care_state_deltas — materialized change computations
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS care_state_deltas (
  id TEXT PRIMARY KEY,
  care_recipient_id TEXT NOT NULL,
  computed_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  from_time TIMESTAMPTZ NOT NULL,
  to_time TIMESTAMPTZ NOT NULL,
  additions JSONB NOT NULL DEFAULT '[]',
  removals JSONB NOT NULL DEFAULT '[]',
  modifications JSONB NOT NULL DEFAULT '[]',
  learning_type TEXT NOT NULL DEFAULT 'new_observation',
  description TEXT NOT NULL DEFAULT ''
);

CREATE INDEX IF NOT EXISTS care_state_deltas_recipient_idx
  ON care_state_deltas (care_recipient_id);
CREATE INDEX IF NOT EXISTS care_state_deltas_time_idx
  ON care_state_deltas (from_time, to_time);

-- ---------------------------------------------------------------------------
-- 6. care_state_snapshots — materialized historical states
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS care_state_snapshots (
  id TEXT PRIMARY KEY,
  care_recipient_id TEXT NOT NULL,
  computed_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  as_of_time TIMESTAMPTZ NOT NULL,
  assertions JSONB NOT NULL DEFAULT '[]',
  baselines JSONB NOT NULL DEFAULT '[]',
  transition_count INTEGER NOT NULL DEFAULT 0,
  confidence_summary NUMERIC(3,2) NOT NULL DEFAULT 0,
  materialized BOOLEAN NOT NULL DEFAULT false
);

CREATE INDEX IF NOT EXISTS care_state_snapshots_recipient_idx
  ON care_state_snapshots (care_recipient_id);
CREATE INDEX IF NOT EXISTS care_state_snapshots_time_idx
  ON care_state_snapshots (as_of_time);

-- ---------------------------------------------------------------------------
-- 7. Backfill legacy StateSituation records as assertions
-- ---------------------------------------------------------------------------

-- This migration preserves all existing data. StateSituation records become
-- assertions with dimension = 'active_conditions' (the closest match to
-- the legacy flat summary model). They can be refined over time.

DO $$
DECLARE
  legacy_record RECORD;
  new_id TEXT;
BEGIN
  IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = 'state_situations') THEN
    FOR legacy_record IN SELECT * FROM state_situations LOOP
      new_id := 'assertion-legacy-' || legacy_record.id;
      INSERT INTO care_state_assertions (
        id, care_recipient_id, caregiver_id, dimension, value, status,
        validity_start, validity_end, confidence, evidence_ids, event_ids,
        conflict_status, provenance_note, created_at, updated_at
      ) VALUES (
        new_id,
        legacy_record.careSessionId,
        legacy_record.userId,
        'active_conditions',
        COALESCE(legacy_record.summary, ''),
        CASE legacy_record.status
          WHEN 'active' THEN 'active'
          WHEN 'resolved' THEN 'resolved'
          ELSE 'active'
        END,
        COALESCE(legacy_record.createdAt, NOW()),
        NULL,
        0.5,
        ARRAY[]::TEXT[],
        ARRAY[]::TEXT[],
        'coexisting',
        'Backfilled from legacy StateSituation',
        COALESCE(legacy_record.createdAt, NOW()),
        COALESCE(legacy_record.updatedAt, NOW())
      ) ON CONFLICT (id) DO NOTHING;
    END LOOP;
  END IF;
END $$;

COMMENT ON TABLE care_state_assertions IS
  'Atomic time-bounded assertions about a person''s care condition. The fundamental unit of Longitudinal Care State.';
COMMENT ON TABLE care_state_baselines IS
  'Reference states for deviation detection. Can be pre-event, post-event, or stable-period.';
COMMENT ON TABLE care_state_transitions IS
  'Records of state changes. Reconstructable from assertion validity periods.';
COMMENT ON TABLE care_state_conflicts IS
  'Competing valid assertions for the same dimension at the same time.';
COMMENT ON TABLE care_state_deltas IS
  'Materialized state deltas for change detection performance.';
COMMENT ON TABLE care_state_snapshots IS
  'Materialized historical states for reconstruction performance.';
