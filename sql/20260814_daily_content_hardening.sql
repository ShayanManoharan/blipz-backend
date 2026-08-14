-- Blipz daily-content technical integrity and provenance.
--
-- REVIEW/APPLY SEPARATELY. This migration is additive and intentionally leaves every
-- historical daily_content/fallback_daily_content row untouched. NULL provenance means
-- "legacy/unvalidated under the current contract"; application publication gates must
-- reject such rows rather than manufacturing provenance during migration.

BEGIN;

ALTER TABLE daily_content
  ADD COLUMN IF NOT EXISTS content_schema_version INTEGER,
  ADD COLUMN IF NOT EXISTS generator_version TEXT,
  ADD COLUMN IF NOT EXISTS validated_at TIMESTAMPTZ,
  ADD COLUMN IF NOT EXISTS validation_result JSONB,
  ADD COLUMN IF NOT EXISTS package_revision_id UUID,
  ADD COLUMN IF NOT EXISTS original_image_prompt TEXT,
  ADD COLUMN IF NOT EXISTS effective_image_prompt TEXT,
  ADD COLUMN IF NOT EXISTS image_storage_key TEXT,
  ADD COLUMN IF NOT EXISTS image_sha256 TEXT,
  ADD COLUMN IF NOT EXISTS image_model TEXT,
  ADD COLUMN IF NOT EXISTS image_generated_at TIMESTAMPTZ,
  ADD COLUMN IF NOT EXISTS image_verified_at TIMESTAMPTZ;

ALTER TABLE fallback_daily_content
  ADD COLUMN IF NOT EXISTS content_schema_version INTEGER,
  ADD COLUMN IF NOT EXISTS generator_version TEXT,
  ADD COLUMN IF NOT EXISTS validated_at TIMESTAMPTZ,
  ADD COLUMN IF NOT EXISTS validation_result JSONB,
  ADD COLUMN IF NOT EXISTS package_revision_id UUID,
  ADD COLUMN IF NOT EXISTS original_image_prompt TEXT,
  ADD COLUMN IF NOT EXISTS effective_image_prompt TEXT,
  ADD COLUMN IF NOT EXISTS image_storage_key TEXT,
  ADD COLUMN IF NOT EXISTS image_sha256 TEXT,
  ADD COLUMN IF NOT EXISTS image_model TEXT,
  ADD COLUMN IF NOT EXISTS image_generated_at TIMESTAMPTZ,
  ADD COLUMN IF NOT EXISTS image_verified_at TIMESTAMPTZ;

ALTER TABLE daily_content_generation_log
  ADD COLUMN IF NOT EXISTS package_revision_id UUID,
  ADD COLUMN IF NOT EXISTS fallback_source_id UUID,
  ADD COLUMN IF NOT EXISTS validation_result JSONB;

-- Captures every deliberate replacement of a ready/published package. The existing
-- daily_content row remains the date-addressed publication record, while each approved
-- replacement receives a new package_revision_id and an append-only audit event.
CREATE TABLE IF NOT EXISTS daily_content_replacement_log (
  id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
  content_date DATE NOT NULL,
  daily_content_id UUID NOT NULL REFERENCES daily_content(id),
  previous_package_revision_id UUID,
  replacement_package_revision_id UUID NOT NULL,
  actor TEXT NOT NULL,
  reason TEXT NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS daily_content_replacement_log_date_idx
  ON daily_content_replacement_log (content_date, created_at DESC);

CREATE INDEX IF NOT EXISTS daily_content_package_revision_idx
  ON daily_content (package_revision_id);

CREATE INDEX IF NOT EXISTS fallback_daily_content_package_revision_idx
  ON fallback_daily_content (package_revision_id);

-- These constraints apply only to new/provenanced rows because PostgreSQL CHECK
-- constraints accept NULL. The application validator supplies the stronger all-fields
-- required-together rule before ready/publish/activation.
DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint WHERE conname = 'daily_content_positive_schema_version'
  ) THEN
    ALTER TABLE daily_content ADD CONSTRAINT daily_content_positive_schema_version
      CHECK (content_schema_version IS NULL OR content_schema_version > 0);
  END IF;
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint WHERE conname = 'daily_content_image_sha256_format'
  ) THEN
    ALTER TABLE daily_content ADD CONSTRAINT daily_content_image_sha256_format
      CHECK (image_sha256 IS NULL OR image_sha256 ~ '^[0-9a-f]{64}$');
  END IF;
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint WHERE conname = 'fallback_daily_content_positive_schema_version'
  ) THEN
    ALTER TABLE fallback_daily_content ADD CONSTRAINT fallback_daily_content_positive_schema_version
      CHECK (content_schema_version IS NULL OR content_schema_version > 0);
  END IF;
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint WHERE conname = 'fallback_daily_content_image_sha256_format'
  ) THEN
    ALTER TABLE fallback_daily_content ADD CONSTRAINT fallback_daily_content_image_sha256_format
      CHECK (image_sha256 IS NULL OR image_sha256 ~ '^[0-9a-f]{64}$');
  END IF;
END
$$;

COMMIT;
