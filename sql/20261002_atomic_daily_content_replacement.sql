-- Atomic, audited replacement for an already-ready or published daily package.
--
-- REVIEW/APPLY SEPARATELY. This migration is additive. It does not update or delete
-- daily_content, scores, users, attempts, or historical replacement records.

BEGIN;

ALTER TABLE daily_content_replacement_log
  ADD COLUMN IF NOT EXISTS previous_image_storage_key TEXT,
  ADD COLUMN IF NOT EXISTS replacement_image_storage_key TEXT,
  ADD COLUMN IF NOT EXISTS previous_image_sha256 TEXT,
  ADD COLUMN IF NOT EXISTS replacement_image_sha256 TEXT,
  ADD COLUMN IF NOT EXISTS forced BOOLEAN NOT NULL DEFAULT FALSE,
  ADD COLUMN IF NOT EXISTS completed_attempt_count INTEGER NOT NULL DEFAULT 0;

CREATE OR REPLACE FUNCTION public.replace_daily_content_revision(
  p_daily_content_id UUID,
  p_expected_revision_id UUID,
  p_replacement JSONB,
  p_actor TEXT,
  p_reason TEXT,
  p_force BOOLEAN DEFAULT FALSE
)
RETURNS SETOF public.daily_content
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog
AS $$
DECLARE
  current_package public.daily_content%ROWTYPE;
  new_revision_id UUID;
  new_storage_key TEXT;
  completed_attempts INTEGER;
BEGIN
  IF p_actor IS NULL OR btrim(p_actor) = '' THEN
    RAISE EXCEPTION USING ERRCODE = '22023', MESSAGE = 'replacement actor is required';
  END IF;
  IF p_reason IS NULL OR btrim(p_reason) = '' THEN
    RAISE EXCEPTION USING ERRCODE = '22023', MESSAGE = 'replacement reason is required';
  END IF;
  IF COALESCE((p_replacement -> 'validation_result' ->> 'valid')::BOOLEAN, FALSE) IS NOT TRUE THEN
    RAISE EXCEPTION USING ERRCODE = '22023', MESSAGE = 'replacement must have a successful validation result';
  END IF;

  new_revision_id := (p_replacement ->> 'package_revision_id')::UUID;
  new_storage_key := p_replacement ->> 'image_storage_key';
  IF new_revision_id IS NULL THEN
    RAISE EXCEPTION USING ERRCODE = '22023', MESSAGE = 'replacement package revision is required';
  END IF;
  IF new_storage_key IS NULL OR btrim(new_storage_key) = '' THEN
    RAISE EXCEPTION USING ERRCODE = '22023', MESSAGE = 'replacement image storage key is required';
  END IF;

  SELECT *
  INTO current_package
  FROM public.daily_content
  WHERE id = p_daily_content_id
  FOR UPDATE;

  IF NOT FOUND THEN
    RAISE EXCEPTION USING ERRCODE = 'P0002', MESSAGE = 'daily content package not found';
  END IF;
  IF current_package.status NOT IN ('ready', 'published') THEN
    RAISE EXCEPTION USING ERRCODE = '55000', MESSAGE = 'only ready or published content can use replacement workflow';
  END IF;
  IF current_package.package_revision_id IS DISTINCT FROM p_expected_revision_id THEN
    RAISE EXCEPTION USING ERRCODE = 'P0001', MESSAGE = 'daily content revision changed before replacement';
  END IF;
  IF current_package.package_revision_id IS NOT NULL
     AND current_package.package_revision_id = new_revision_id THEN
    RAISE EXCEPTION USING ERRCODE = '22023', MESSAGE = 'replacement must use a new package revision';
  END IF;
  IF current_package.image_storage_key IS NOT NULL
     AND current_package.image_storage_key = new_storage_key THEN
    RAISE EXCEPTION USING ERRCODE = '22023', MESSAGE = 'replacement must use a new image storage key';
  END IF;

  SELECT COUNT(*)::INTEGER
  INTO completed_attempts
  FROM public.scores
  WHERE date = current_package.date
    AND (maths_completed OR guess_completed OR trivia_completed);

  IF current_package.status = 'published' AND completed_attempts > 0 AND NOT p_force THEN
    RAISE EXCEPTION USING
      ERRCODE = 'P0001',
      MESSAGE = 'published content has completed attempts; explicit force is required';
  END IF;

  UPDATE public.daily_content
  SET image_url = p_replacement ->> 'image_url',
      image_prompt = p_replacement ->> 'image_prompt',
      trivia_questions = p_replacement -> 'trivia_questions',
      math_problems = p_replacement -> 'math_problems',
      generated_at = NOW(),
      published_at = CASE
        WHEN current_package.status = 'published' THEN NOW()
        ELSE current_package.published_at
      END,
      is_fallback = FALSE,
      fallback_source_id = NULL,
      content_schema_version = (p_replacement ->> 'content_schema_version')::INTEGER,
      generator_version = p_replacement ->> 'generator_version',
      validated_at = (p_replacement ->> 'validated_at')::TIMESTAMPTZ,
      validation_result = p_replacement -> 'validation_result',
      package_revision_id = new_revision_id,
      original_image_prompt = p_replacement ->> 'original_image_prompt',
      effective_image_prompt = p_replacement ->> 'effective_image_prompt',
      image_storage_key = new_storage_key,
      image_sha256 = p_replacement ->> 'image_sha256',
      image_model = p_replacement ->> 'image_model',
      image_generated_at = (p_replacement ->> 'image_generated_at')::TIMESTAMPTZ,
      image_verified_at = (p_replacement ->> 'image_verified_at')::TIMESTAMPTZ
  WHERE id = current_package.id;

  INSERT INTO public.daily_content_replacement_log (
    content_date,
    daily_content_id,
    previous_package_revision_id,
    replacement_package_revision_id,
    actor,
    reason,
    previous_image_storage_key,
    replacement_image_storage_key,
    previous_image_sha256,
    replacement_image_sha256,
    forced,
    completed_attempt_count
  ) VALUES (
    current_package.date,
    current_package.id,
    current_package.package_revision_id,
    new_revision_id,
    btrim(p_actor),
    btrim(p_reason),
    current_package.image_storage_key,
    new_storage_key,
    current_package.image_sha256,
    p_replacement ->> 'image_sha256',
    p_force,
    completed_attempts
  );

  RETURN QUERY
  SELECT * FROM public.daily_content WHERE id = current_package.id;
END;
$$;

REVOKE ALL ON FUNCTION public.replace_daily_content_revision(UUID, UUID, JSONB, TEXT, TEXT, BOOLEAN)
  FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.replace_daily_content_revision(UUID, UUID, JSONB, TEXT, TEXT, BOOLEAN)
  TO service_role;

COMMIT;
