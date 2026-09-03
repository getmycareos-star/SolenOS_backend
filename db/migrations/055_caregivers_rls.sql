-- ============================================================
-- Migration 055: RLS on caregivers — authenticated users own their row
-- ============================================================

-- Enable RLS
ALTER TABLE caregivers ENABLE ROW LEVEL SECURITY;

-- Drop existing policies if any (idempotent)
DROP POLICY IF EXISTS "caregivers_select_own" ON caregivers;
DROP POLICY IF EXISTS "caregivers_insert_own" ON caregivers;
DROP POLICY IF EXISTS "caregivers_update_own" ON caregivers;
DROP POLICY IF EXISTS "caregivers_delete_own" ON caregivers;

-- SELECT: users can read only their own row
CREATE POLICY "caregivers_select_own" ON caregivers
  FOR SELECT
  TO authenticated
  USING (id = auth.uid()::text);

-- INSERT: users can create only their own row (id must match auth.uid())
CREATE POLICY "caregivers_insert_own" ON caregivers
  FOR INSERT
  TO authenticated
  WITH CHECK (id = auth.uid()::text);

-- UPDATE: users can modify only their own row
CREATE POLICY "caregivers_update_own" ON caregivers
  FOR UPDATE
  TO authenticated
  USING (id = auth.uid()::text)
  WITH CHECK (id = auth.uid()::text);

-- DELETE: users can delete only their own row
CREATE POLICY "caregivers_delete_own" ON caregivers
  FOR DELETE
  TO authenticated
  USING (id = auth.uid()::text);

-- Service role bypass (for backend operations)
-- The service_role JWT automatically bypasses RLS in Supabase
