-- Existing pattern/situation confidence values were uncalibrated constants or
-- derived from those constants. Preserve the observations and pattern strength,
-- but remove the misleading numeric confidence values.
ALTER TABLE IF EXISTS patterns
  ALTER COLUMN confidence DROP NOT NULL;
ALTER TABLE IF EXISTS patterns
  ALTER COLUMN confidence DROP DEFAULT;
UPDATE patterns SET confidence = NULL;

ALTER TABLE IF EXISTS situations
  ALTER COLUMN confidence DROP NOT NULL;
ALTER TABLE IF EXISTS situations
  ALTER COLUMN confidence DROP DEFAULT;
UPDATE situations SET confidence = NULL;
