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