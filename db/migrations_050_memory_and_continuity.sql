-- ============================================================
-- Migration 051: care_episodes — memory layer persistence
-- ============================================================

CREATE TABLE IF NOT EXISTS care_episodes (
  id TEXT PRIMARY KEY,
  care_recipient_id TEXT NOT NULL,
  kind TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'resolved', 'archived')),
  summary TEXT,
  event_ids JSONB NOT NULL DEFAULT '[]'::jsonb,
  opened_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  closed_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_care_episodes_recipient
  ON care_episodes (care_recipient_id);

CREATE INDEX IF NOT EXISTS idx_care_episodes_status
  ON care_episodes (care_recipient_id, status);

-- ============================================================
-- Migration 052: continuity_links — structured continuity layer
-- ============================================================

CREATE TABLE IF NOT EXISTS continuity_links (
  id TEXT PRIMARY KEY,
  care_recipient_id TEXT NOT NULL,
  from_event_id TEXT NOT NULL,
  to_event_id TEXT NOT NULL,
  link_type TEXT NOT NULL,
  weight NUMERIC NOT NULL DEFAULT 0.5,
  metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_continuity_links_recipient
  ON continuity_links (care_recipient_id);

CREATE INDEX IF NOT EXISTS idx_continuity_links_from
  ON continuity_links (from_event_id);

CREATE INDEX IF NOT EXISTS idx_continuity_links_to
  ON continuity_links (to_event_id);

-- ============================================================
-- Migration 053: unresolved_questions — persistent uncertainty register
-- ============================================================

CREATE TABLE IF NOT EXISTS unresolved_questions (
  id TEXT PRIMARY KEY,
  care_recipient_id TEXT NOT NULL,
  question TEXT NOT NULL,
  source_event_id TEXT,
  status TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'resolved', 'invalidated')),
  resolved_at TIMESTAMPTZ,
  resolution_event_id TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_unresolved_questions_recipient
  ON unresolved_questions (care_recipient_id);

CREATE INDEX IF NOT EXISTS idx_unresolved_questions_status
  ON unresolved_questions (care_recipient_id, status);
