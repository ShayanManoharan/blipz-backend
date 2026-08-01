-- Users table
CREATE TABLE users (
  id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
  username TEXT UNIQUE NOT NULL,
  email TEXT UNIQUE NOT NULL,
  created_at TIMESTAMP DEFAULT NOW(),
  current_streak INT DEFAULT 0,
  longest_streak INT DEFAULT 0
);

-- Daily content table
CREATE TABLE daily_content (
  id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
  date DATE UNIQUE NOT NULL,
  image_url TEXT NOT NULL,
  image_prompt TEXT NOT NULL,
  trivia_questions JSONB NOT NULL,
  created_at TIMESTAMP DEFAULT NOW()
);

-- Scores table
CREATE TABLE scores (
  id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
  user_id UUID REFERENCES users(id),
  date DATE NOT NULL,
  maths_score INT DEFAULT 0,
  trivia_score INT DEFAULT 0,
  guess_score DECIMAL(3,1) DEFAULT 0,
  total_score DECIMAL(5,1) DEFAULT 0,
  created_at TIMESTAMP DEFAULT NOW(),
  UNIQUE(user_id, date)
);

-- Friends table
CREATE TABLE friends (
  id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
  user_id UUID REFERENCES users(id),
  friend_id UUID REFERENCES users(id),
  created_at TIMESTAMP DEFAULT NOW(),
  UNIQUE(user_id, friend_id)
);

-- Add math problems to daily content
ALTER TABLE daily_content
ADD COLUMN math_problems JSONB;

-- Anonymous Supabase auth users have no email, so it can no longer be required
ALTER TABLE users ALTER COLUMN email DROP NOT NULL;

-- Auto-create a public.users row (same id as the auth user) on every sign-in,
-- so scores/friends FK constraints are always satisfiable with no app-level race.
CREATE OR REPLACE FUNCTION public.handle_new_user()
RETURNS trigger AS $$
BEGIN
  INSERT INTO public.users (id, username, email)
  VALUES (NEW.id, 'guest_' || substr(NEW.id::text, 1, 8), NEW.email)
  ON CONFLICT (id) DO NOTHING;
  RETURN NEW;
END;
$$ LANGUAGE plpgsql SECURITY DEFINER;

CREATE TRIGGER on_auth_user_created
  AFTER INSERT ON auth.users
  FOR EACH ROW EXECUTE FUNCTION public.handle_new_user();

-- Cache the Leaderboard Narrator's generated message once per day
ALTER TABLE daily_content
ADD COLUMN daily_message TEXT;

-- Fix schema drift: scores.user_id and friends.user_id/friend_id were actually
-- created as plain TEXT with no foreign key (despite this file's original
-- CREATE TABLE statements above showing UUID + REFERENCES) - discovered because
-- PostgREST couldn't embed users(username) in leaderboard joins ("Could not
-- find a relationship between 'scores' and 'users'"). Run this after clearing
-- any non-UUID test data from these columns.
ALTER TABLE scores
  ALTER COLUMN user_id TYPE UUID USING user_id::uuid,
  ADD CONSTRAINT scores_user_id_fkey FOREIGN KEY (user_id) REFERENCES users(id);

ALTER TABLE friends
  ALTER COLUMN user_id TYPE UUID USING user_id::uuid,
  ALTER COLUMN friend_id TYPE UUID USING friend_id::uuid,
  ADD CONSTRAINT friends_user_id_fkey FOREIGN KEY (user_id) REFERENCES users(id),
  ADD CONSTRAINT friends_friend_id_fkey FOREIGN KEY (friend_id) REFERENCES users(id);

-- Explicit per-game completion state (transitional design — see PRODUCTION_AUDIT.md
-- and ROADMAP.md for why a full daily_game_attempts table was deferred: leaderboard,
-- streak, and Today/Profile read paths are all built around one flat scores row per
-- user/day, and reworking that aggregation is disproportionate churn relative to what
-- fixing the actual security gap (idempotent completion truth) requires. These columns
-- replace the score > 0 / mathsScore == 20 heuristics with real persisted state, so a
-- legitimate zero score is correctly distinguishable from "never played."
ALTER TABLE scores
  ADD COLUMN maths_completed BOOLEAN NOT NULL DEFAULT FALSE,
  ADD COLUMN guess_completed BOOLEAN NOT NULL DEFAULT FALSE,
  ADD COLUMN trivia_completed BOOLEAN NOT NULL DEFAULT FALSE,
  ADD COLUMN maths_elapsed_seconds DOUBLE PRECISION,
  ADD COLUMN guess_text TEXT,
  ADD COLUMN trivia_answers JSONB;

-- One-time backfill for rows that predate the columns above, using the same
-- heuristics we're retiring going forward. This is legacy-data reconciliation only —
-- application code must never use score > 0 / mathsScore == 20 as completion logic
-- after this point.
UPDATE scores SET maths_completed = TRUE WHERE maths_score = 20;
UPDATE scores SET guess_completed = TRUE WHERE guess_score > 0;
UPDATE scores SET trivia_completed = TRUE WHERE trivia_score > 0;

-- NOTE: this migration does NOT touch existing daily_content.math_problems rows.
-- The stored shape is changing at the application level from {question, answer} to
-- {left_operand, right_operand, operation} (see app/agents/content_generator.py) so
-- the client can compute the expected answer locally instead of receiving a separate
-- answer key. Any daily_content row generated before this change keeps the old shape
-- and will fail to grade correctly against the new code — regenerate today's content
-- (POST /games/generate-daily-content) after applying this migration and deploying.

-- B23 fix: closes the Guess concurrency-cost window (see PRODUCTION_AUDIT.md B23/B2) —
-- two simultaneous first-time Guess submissions could previously both call OpenAI
-- before complete_game_attempt's compare-and-swap ensured only one result was ever
-- persisted. guess_status/guess_scoring_started_at let the backend atomically reserve
-- the right to call OpenAI at all, so a concurrent request never independently starts
-- its own scoring call. See app/routers/games.py's acquire_guess_scoring_slot().
ALTER TABLE scores
  ADD COLUMN IF NOT EXISTS guess_status TEXT NOT NULL DEFAULT 'not_started'
    CHECK (guess_status IN ('not_started', 'scoring', 'completed', 'failed')),
  ADD COLUMN IF NOT EXISTS guess_scoring_started_at TIMESTAMPTZ;

-- One-time backfill so already-completed rows read as 'completed' rather than the
-- column default — purely cosmetic/consistency, guess_completed remains the actual
-- source of truth for "is Guess done" everywhere in the application.
UPDATE scores SET guess_status = 'completed' WHERE guess_completed = TRUE;

-- Hosted-deployment slice: separates generating a day's content from publishing it
-- (see PRODUCTION_AUDIT.md's deployment plan). `daily_content.date` remains the
-- canonical "content_date" (already UNIQUE NOT NULL since the very first migration —
-- one row per date was already guaranteed at the DB level). `status` gates whether
-- GET /games/daily-content may serve a row at all, independent of whether it exists:
-- a row can be fully generated ('ready') well before its date, without going live,
-- and publication is a separate, idempotent, explicitly-triggered step.
--
-- DEFAULT is 'draft', not 'published': a fail-safe default for a production content
-- pipeline means an insert that forgets to specify status can never accidentally
-- become publicly servable. generate_content_for_date/activate_fallback_for_date
-- always set status explicitly (never rely on this default) — see app/agents/
-- content_generator.py. The 5 pre-existing rows are explicitly backfilled to
-- 'published' below, not left to any default, since they were historically already live.
ALTER TABLE daily_content
  ADD COLUMN IF NOT EXISTS status TEXT NOT NULL DEFAULT 'draft'
    CHECK (status IN ('draft', 'ready', 'published', 'failed')),
  ADD COLUMN IF NOT EXISTS generated_at TIMESTAMPTZ,
  ADD COLUMN IF NOT EXISTS published_at TIMESTAMPTZ,
  ADD COLUMN IF NOT EXISTS is_fallback BOOLEAN NOT NULL DEFAULT FALSE,
  ADD COLUMN IF NOT EXISTS fallback_source_id UUID;

-- Explicit backfill, scoped to the exact 5 rows that existed before this migration —
-- NOT `WHERE status = 'draft'`. A status-based match would also catch any legitimate
-- future draft row if this block were ever rerun (e.g. mid `/admin/replace-content`,
-- which sets status back to 'draft' before regenerating), silently publishing content
-- that was never meant to go live yet. Matching by id is exact and rerun-safe
-- regardless of what state any other row is in, now or later — a rerun just resets
-- these same 5 rows to the same values again (a harmless no-op).
UPDATE daily_content
SET status = 'published',
    generated_at = created_at,
    published_at = created_at
WHERE id IN (
  'fc47ca77-0ff8-491f-8bb1-66109226f3bb',
  '89772dd4-be83-477a-a2bd-ad10d440771f',
  'bbe3fcdf-c906-4f3b-a163-0535c8360600',
  '347498f2-9294-494e-9071-24ea61ed2c84',
  '621d76f0-63cb-4251-b952-880bad96606c'
);

-- Audit trail for the generate/publish pipeline — deliberately a separate table from
-- daily_content so a failed attempt never risks violating daily_content's
-- UNIQUE(date) constraint or leaving a partial/placeholder content row. Powers the
-- "latest generation status" observability endpoint.
CREATE TABLE IF NOT EXISTS daily_content_generation_log (
  id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
  content_date DATE NOT NULL,
  operation TEXT NOT NULL CHECK (operation IN ('generate', 'publish')),
  status TEXT NOT NULL CHECK (status IN ('success', 'failed')),
  used_fallback BOOLEAN NOT NULL DEFAULT FALSE,
  error_message TEXT,
  attempted_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS daily_content_generation_log_content_date_idx
  ON daily_content_generation_log (content_date, attempted_at DESC);

-- Small prevalidated emergency pool used when real generation fails for a date that's
-- about to be needed. `active = FALSE` lets a problematic fallback package be pulled
-- from rotation without deleting its history. `last_used_date`/`times_used` support
-- picking the least-recently-used entry so the same fallback doesn't repeat on
-- consecutive days when another active entry is available.
CREATE TABLE IF NOT EXISTS fallback_daily_content (
  id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
  label TEXT NOT NULL,
  image_url TEXT NOT NULL,
  image_prompt TEXT NOT NULL,
  trivia_questions JSONB NOT NULL,
  math_problems JSONB NOT NULL,
  active BOOLEAN NOT NULL DEFAULT TRUE,
  last_used_date DATE,
  times_used INT NOT NULL DEFAULT 0,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- NOTE: this migration does not seed fallback_daily_content — no rows means the
-- fallback system has nothing to activate (publish_content_for_date will surface a
-- clear "no fallback available" failure rather than silently doing nothing). Seed via
-- POST /admin/seed-fallback-content (see app/routers/admin_content.py), which inserts
-- hand-authored trivia/math and a static placeholder image URL — it does not call
-- OpenAI, so seeding never incurs image-generation cost. Replacing the placeholder
-- image with a real generated one later is a separate, explicit, approved action.