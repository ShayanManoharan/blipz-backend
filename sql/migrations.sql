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