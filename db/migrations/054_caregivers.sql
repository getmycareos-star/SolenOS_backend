-- ============================================================
-- Migration 054: caregivers — JWT authentication and access control
-- ============================================================

CREATE TABLE IF NOT EXISTS caregivers (
  id TEXT PRIMARY KEY,
  email TEXT UNIQUE NOT NULL,
  password_hash TEXT NOT NULL,
  display_name TEXT NOT NULL,
  care_recipient_ids JSONB NOT NULL DEFAULT '[]'::jsonb,
  role TEXT NOT NULL DEFAULT 'caregiver' CHECK (role IN ('caregiver', 'admin')),
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  last_login_at TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_caregivers_email ON caregivers (email);
CREATE INDEX IF NOT EXISTS idx_caregivers_role ON caregivers (role);
