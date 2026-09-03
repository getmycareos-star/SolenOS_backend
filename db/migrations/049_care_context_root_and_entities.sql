-- ============================================================
-- Migration 049: care_context_root — Living Care Record spine
-- ============================================================

CREATE TABLE IF NOT EXISTS care_context_root (
  id TEXT PRIMARY KEY,
  care_recipient_id TEXT NOT NULL,
  caregiver_id TEXT NOT NULL,
  root_event_id TEXT,
  multi_caregiver JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_care_context_root_recipient
  ON care_context_root (care_recipient_id);

CREATE INDEX IF NOT EXISTS idx_care_context_root_caregiver
  ON care_context_root (caregiver_id);

-- ============================================================
-- Migration 050: care_entities — resolved persons, conditions, medications, providers
-- ============================================================

CREATE TABLE IF NOT EXISTS care_entities (
  id TEXT PRIMARY KEY,
  care_recipient_id TEXT NOT NULL,
  entity_type TEXT NOT NULL CHECK (entity_type IN ('person', 'condition', 'medication', 'provider', 'institution')),
  canonical_name TEXT NOT NULL,
  aliases JSONB NOT NULL DEFAULT '[]'::jsonb,
  confidence_tag TEXT NOT NULL DEFAULT 'inferred' CHECK (confidence_tag IN ('confirmed', 'reported', 'inferred', 'unknown', 'contradictory')),
  source_event_ids JSONB NOT NULL DEFAULT '[]'::jsonb,
  status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'resolved', 'invalidated')),
  metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_care_entities_recipient
  ON care_entities (care_recipient_id);

CREATE INDEX IF NOT EXISTS idx_care_entities_type
  ON care_entities (care_recipient_id, entity_type);

CREATE INDEX IF NOT EXISTS idx_care_entities_canonical
  ON care_entities (care_recipient_id, entity_type, canonical_name);

-- Table: care_record_events — full CanonicalCareEvent records
CREATE TABLE IF NOT EXISTS care_record_events (
  id TEXT PRIMARY KEY,
  care_recipient_id TEXT NOT NULL,
  event_data JSONB NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_care_record_events_recipient
  ON care_record_events (care_recipient_id);
