-- Confidence values in changes, patterns and pattern-derived situations were
-- uncalibrated heuristic scores. Preserve the underlying records and evidence,
-- but expose confidence as unknown until a validated calibration method exists.
DO $$
BEGIN
  IF to_regclass('public.reasoning_memory') IS NOT NULL THEN
    ALTER TABLE public.reasoning_memory ALTER COLUMN confidence DROP NOT NULL;
    ALTER TABLE public.reasoning_memory ALTER COLUMN confidence DROP DEFAULT;
    UPDATE public.reasoning_memory SET confidence = NULL;
  END IF;

  IF to_regclass('public.changes') IS NOT NULL THEN
    ALTER TABLE public.changes ALTER COLUMN confidence DROP NOT NULL;
    ALTER TABLE public.changes ALTER COLUMN confidence DROP DEFAULT;
    UPDATE public.changes SET confidence = NULL;
  END IF;

  IF to_regclass('public.patterns') IS NOT NULL THEN
    ALTER TABLE public.patterns ALTER COLUMN confidence DROP NOT NULL;
    ALTER TABLE public.patterns ALTER COLUMN confidence DROP DEFAULT;
    UPDATE public.patterns SET confidence = NULL;
  END IF;

  IF to_regclass('public.situations') IS NOT NULL THEN
    ALTER TABLE public.situations ALTER COLUMN confidence DROP NOT NULL;
    ALTER TABLE public.situations ALTER COLUMN confidence DROP DEFAULT;
    UPDATE public.situations SET confidence = NULL;
  END IF;
END $$;
