-- REVIEW/APPLY SEPARATELY. Additive V2 only; never alters or backfills V1 tables.
BEGIN;
DO $$ BEGIN
  IF to_regclass('public.users') IS NULL OR to_regclass('public.scores') IS NULL
     OR NOT EXISTS (SELECT 1 FROM pg_attribute WHERE attrelid='public.users'::regclass AND attname='id' AND atttypid='uuid'::regtype)
     OR NOT EXISTS (SELECT 1 FROM pg_attribute WHERE attrelid='public.scores'::regclass AND attname='guess_completed') THEN
    RAISE EXCEPTION 'Expected hardened V1 users/scores prerequisites are missing';
  END IF;
END $$;

CREATE SCHEMA blipz_v2;
CREATE SCHEMA blipz_v2_private;
REVOKE ALL ON SCHEMA blipz_v2, blipz_v2_private FROM PUBLIC, anon, authenticated, service_role;
GRANT USAGE ON SCHEMA blipz_v2 TO service_role;
-- No client access policies: these schemas are backend-only, not Supabase client APIs.
ALTER DEFAULT PRIVILEGES IN SCHEMA blipz_v2 REVOKE EXECUTE ON FUNCTIONS FROM PUBLIC;
ALTER DEFAULT PRIVILEGES IN SCHEMA blipz_v2_private REVOKE EXECUTE ON FUNCTIONS FROM PUBLIC;

CREATE TABLE blipz_v2.game_content_revisions (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  game_type text NOT NULL CHECK (btrim(game_type)<>''),
  variant text,
  schema_version integer NOT NULL CHECK (schema_version>0),
  generator_version text NOT NULL CHECK (btrim(generator_version)<>''),
  status text NOT NULL CHECK (status IN ('draft','validated','approved','retired')),
  public_payload jsonb NOT NULL CHECK (jsonb_typeof(public_payload)='object' AND public_payload->>'kind' IS NOT DISTINCT FROM game_type),
  content_sha256 text NOT NULL CHECK (content_sha256 ~ '^[0-9a-f]{64}$'),
  validation jsonb NOT NULL CHECK (jsonb_typeof(validation)='object'),
  validated_at timestamptz,
  approved_by text,
  approved_at timestamptz,
  created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  UNIQUE(id,game_type),
  CHECK (status<>'approved' OR coalesce((validated_at IS NOT NULL AND approved_at IS NOT NULL AND btrim(approved_by)<>'' AND validation->>'human_approval'='approved'),false))
);
CREATE TABLE blipz_v2_private.game_solutions (
  content_revision_id uuid PRIMARY KEY REFERENCES blipz_v2.game_content_revisions(id),
  solution_payload jsonb NOT NULL CHECK (jsonb_typeof(solution_payload)='object'),
  -- Prepared by trusted Python normalization v1, never supplied by an iOS client.
  -- PostgreSQL 17 lower() is not Python Unicode casefold(). No locale-dependent substitute.
  normalized_aliases text[] NOT NULL DEFAULT '{}',
  normalization_version text NOT NULL DEFAULT 'answer-normalization-v1' CHECK (normalization_version='answer-normalization-v1'),
  CHECK (array_position(normalized_aliases,NULL) IS NULL AND array_position(normalized_aliases,'') IS NULL)
);
CREATE TABLE blipz_v2.fallback_packages (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  label text NOT NULL UNIQUE CHECK (btrim(label)<>''),
  active boolean NOT NULL DEFAULT false,
  created_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
COMMENT ON TABLE blipz_v2.fallback_packages IS 'Referential scaffold only; no rotation, publication or fallback generation implemented.';
CREATE TABLE blipz_v2.daily_challenges (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  date date NOT NULL UNIQUE,
  product_version text NOT NULL DEFAULT 'blipz-v2' CHECK (product_version='blipz-v2'),
  contract_version text NOT NULL DEFAULT 'v2.1',
  scoring_version text NOT NULL DEFAULT 'v2-equal25-v1' CHECK (scoring_version='v2-equal25-v1'),
  ranking_version text NOT NULL DEFAULT 'v2-rank-v1',
  lineup_revision uuid NOT NULL UNIQUE,
  status text NOT NULL CHECK (status IN ('draft','published')),
  opens_at timestamptz NOT NULL,
  closes_at timestamptz NOT NULL,
  published_at timestamptz,
  required_game_count integer NOT NULL DEFAULT 4 CHECK (required_game_count=4),
  max_points_units integer NOT NULL DEFAULT 10000 CHECK (max_points_units=10000),
  is_fallback boolean NOT NULL DEFAULT false,
  fallback_source_id uuid REFERENCES blipz_v2.fallback_packages(id),
  UNIQUE(id,date),
  CHECK (opens_at=(date::timestamp AT TIME ZONE 'UTC')),
  CHECK (closes_at=opens_at+interval '24 hours'),
  CHECK (is_fallback=(fallback_source_id IS NOT NULL)),
  CHECK ((status='published')=(published_at IS NOT NULL))
);
CREATE TABLE blipz_v2.daily_game_instances (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  challenge_id uuid NOT NULL REFERENCES blipz_v2.daily_challenges(id),
  position integer NOT NULL CHECK (position>0),
  game_type text NOT NULL,
  variant text,
  content_revision_id uuid NOT NULL,
  title text NOT NULL CHECK (btrim(title)<>''),
  presentation_role text NOT NULL CHECK (presentation_role IN ('hero','compact')),
  max_points_units integer NOT NULL CHECK (max_points_units=2500),
  answer_policy jsonb NOT NULL CHECK (jsonb_typeof(answer_policy)='object'),
  UNIQUE(challenge_id,position),
  UNIQUE(id,challenge_id,content_revision_id,game_type,max_points_units),
  FOREIGN KEY(content_revision_id,game_type) REFERENCES blipz_v2.game_content_revisions(id,game_type),
  CHECK (coalesce((answer_policy->>'final_submissions')::integer=1 AND (answer_policy->>'choice_count')::integer=4 AND answer_policy->>'hints_enabled'='false',false)),
  CHECK (coalesce((game_type='trivia' AND answer_policy->>'mode'='native_mcq_set' AND (answer_policy->>'assisted_cap_bps')::integer=10000)
     OR (game_type<>'trivia' AND answer_policy->>'mode'='typed_or_optional_choices' AND (answer_policy->>'assisted_cap_bps')::integer=7500),false))
);
CREATE TABLE blipz_v2.daily_results (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id uuid NOT NULL REFERENCES public.users(id),
  challenge_id uuid NOT NULL,
  date date NOT NULL,
  product_version text NOT NULL DEFAULT 'blipz-v2' CHECK (product_version='blipz-v2'),
  scoring_version text NOT NULL DEFAULT 'v2-equal25-v1' CHECK (scoring_version='v2-equal25-v1'),
  state text NOT NULL DEFAULT 'in_progress' CHECK (state IN ('in_progress','completed')),
  completed_game_count integer NOT NULL DEFAULT 0 CHECK (completed_game_count BETWEEN 0 AND 4),
  required_game_count integer NOT NULL DEFAULT 4 CHECK (required_game_count=4),
  total_points_units integer NOT NULL DEFAULT 0 CHECK (total_points_units BETWEEN 0 AND 10000),
  max_points_units integer NOT NULL DEFAULT 10000 CHECK (max_points_units=10000),
  total_elapsed_ms bigint NOT NULL DEFAULT 0 CHECK (total_elapsed_ms>=0),
  completed_at timestamptz,
  overall_eligible boolean NOT NULL DEFAULT false,
  UNIQUE(user_id,challenge_id),
  FOREIGN KEY(challenge_id,date) REFERENCES blipz_v2.daily_challenges(id,date),
  CHECK ((completed_game_count=required_game_count)=(state='completed')),
  CHECK (overall_eligible=(state='completed') AND (completed_at IS NOT NULL)=(state='completed')),
  CHECK (completed_at IS NULL OR (completed_at AT TIME ZONE 'UTC')::date=date)
);
CREATE TABLE blipz_v2.daily_game_attempts (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id uuid NOT NULL REFERENCES public.users(id),
  challenge_id uuid NOT NULL,
  game_instance_id uuid NOT NULL,
  content_revision_id uuid NOT NULL,
  game_type text NOT NULL,
  challenge_date date NOT NULL,
  state text NOT NULL DEFAULT 'active' CHECK (state IN ('active','choices_revealed','completed_correct','completed_incorrect')),
  started_at timestamptz NOT NULL,
  choices_revealed boolean NOT NULL DEFAULT false,
  choices_revealed_at timestamptz,
  completed_at timestamptz,
  elapsed_ms bigint,
  clock_source text NOT NULL DEFAULT 'database_wall_clock' CHECK (clock_source='database_wall_clock'),
  timing_version text NOT NULL DEFAULT 'wall-clock-v1' CHECK (timing_version='wall-clock-v1'),
  answer_mode text CHECK (answer_mode IN ('typed','assisted_mcq','native_mcq')),
  final_answer jsonb,
  correct_count integer,
  question_count integer,
  is_correct boolean,
  accuracy_bps integer,
  max_points_units integer NOT NULL CHECK (max_points_units=2500),
  available_points_units integer NOT NULL DEFAULT 2500 CHECK (available_points_units IN (2500,1875)),
  awarded_points_units integer,
  score_percentage_bps integer,
  scoring_version text NOT NULL DEFAULT 'v2-equal25-v1' CHECK (scoring_version='v2-equal25-v1'),
  grading_version text NOT NULL DEFAULT 'deterministic-alias-v1',
  completion_idempotency_key uuid,
  UNIQUE(user_id,game_instance_id),
  UNIQUE(id,user_id,challenge_date),
  FOREIGN KEY(challenge_id,challenge_date) REFERENCES blipz_v2.daily_challenges(id,date),
  FOREIGN KEY(user_id,challenge_id) REFERENCES blipz_v2.daily_results(user_id,challenge_id),
  FOREIGN KEY(game_instance_id,challenge_id,content_revision_id,game_type,max_points_units)
    REFERENCES blipz_v2.daily_game_instances(id,challenge_id,content_revision_id,game_type,max_points_units),
  CHECK ((started_at AT TIME ZONE 'UTC')::date=challenge_date),
  CHECK (choices_revealed=(choices_revealed_at IS NOT NULL)),
  CHECK (available_points_units=CASE WHEN choices_revealed THEN 1875 ELSE 2500 END),
  CHECK (choices_revealed_at IS NULL OR (game_type<>'trivia' AND choices_revealed_at>=started_at AND (choices_revealed_at AT TIME ZONE 'UTC')::date=challenge_date)),
  CHECK ((state IN ('completed_correct','completed_incorrect') AND num_nulls(completed_at,elapsed_ms,answer_mode,final_answer,correct_count,question_count,is_correct,accuracy_bps,awarded_points_units,score_percentage_bps,completion_idempotency_key)=0)
     OR (state IN ('active','choices_revealed') AND num_nonnulls(completed_at,elapsed_ms,answer_mode,final_answer,correct_count,question_count,is_correct,accuracy_bps,awarded_points_units,score_percentage_bps,completion_idempotency_key)=0)),
  CHECK (state<>'active' OR NOT choices_revealed),
  CHECK (state<>'choices_revealed' OR choices_revealed),
  CHECK (elapsed_ms IS NULL OR (elapsed_ms>=0 AND completed_at>=started_at AND elapsed_ms=floor(extract(epoch FROM (completed_at-started_at))*1000)::bigint)),
  CHECK (completed_at IS NULL OR ((completed_at AT TIME ZONE 'UTC')::date=challenge_date AND (choices_revealed_at IS NULL OR choices_revealed_at<=completed_at))),
  CHECK (correct_count IS NULL OR (correct_count>=0 AND correct_count<=question_count AND question_count>0)),
  CHECK (accuracy_bps IS NULL OR (accuracy_bps=correct_count*10000/question_count AND accuracy_bps BETWEEN 0 AND 10000)),
  CHECK (is_correct IS NULL OR (is_correct=(correct_count=question_count) AND is_correct=(state='completed_correct'))),
  CHECK (awarded_points_units IS NULL OR (awarded_points_units BETWEEN 0 AND available_points_units AND score_percentage_bps=awarded_points_units*10000/max_points_units)),
  CHECK (final_answer IS NULL OR jsonb_typeof(final_answer)='object'),
  CHECK (answer_mode IS NULL OR
    (game_type='trivia' AND answer_mode='native_mcq' AND NOT choices_revealed AND question_count=5 AND final_answer->>'kind'='trivia' AND awarded_points_units=correct_count*500)
    OR (game_type<>'trivia' AND question_count=1 AND answer_mode=CASE WHEN choices_revealed THEN 'assisted_mcq' ELSE 'typed' END
      AND final_answer->>'kind'=CASE WHEN choices_revealed THEN 'choice' ELSE 'typed' END
      AND awarded_points_units=CASE WHEN is_correct THEN available_points_units ELSE 0 END))
);
COMMENT ON COLUMN blipz_v2.daily_game_attempts.state IS 'not_started and expired are read projections, not scored terminal states. Completion at zero points is terminal.';
CREATE TABLE blipz_v2.daily_activity (
  user_id uuid NOT NULL REFERENCES public.users(id),
  date date NOT NULL,
  first_completed_at timestamptz NOT NULL,
  source_product text NOT NULL CHECK (source_product IN ('blipz-v1','blipz-v2')),
  source_attempt_id uuid,
  legacy_score_id uuid REFERENCES public.scores(id),
  streak_rule_version text NOT NULL DEFAULT 'first-completion-utc-v1',
  streak_after integer NOT NULL CHECK (streak_after>=1),
  longest_streak_after integer NOT NULL CHECK (longest_streak_after>=streak_after),
  PRIMARY KEY(user_id,date),
  FOREIGN KEY(source_attempt_id,user_id,date) REFERENCES blipz_v2.daily_game_attempts(id,user_id,challenge_date),
  CHECK ((first_completed_at AT TIME ZONE 'UTC')::date=date),
  CHECK ((source_product='blipz-v2' AND source_attempt_id IS NOT NULL AND legacy_score_id IS NULL) OR (source_product='blipz-v1' AND source_attempt_id IS NULL AND legacy_score_id IS NOT NULL))
);

CREATE INDEX v2_game_ranking ON blipz_v2.daily_game_attempts(game_instance_id,awarded_points_units DESC,choices_revealed,elapsed_ms,completed_at,id) WHERE completed_at IS NOT NULL;
CREATE INDEX v2_trivia_ranking ON blipz_v2.daily_game_attempts(game_instance_id,correct_count DESC,elapsed_ms,completed_at,id) WHERE completed_at IS NOT NULL;
CREATE INDEX v2_overall_ranking ON blipz_v2.daily_results(challenge_id,total_points_units DESC,total_elapsed_ms,completed_at,id) WHERE overall_eligible;
CREATE INDEX v2_history ON blipz_v2.daily_results(user_id,date DESC,id);
CREATE INDEX v2_revision_instances ON blipz_v2.daily_game_instances(content_revision_id);
CREATE INDEX v2_user_progress ON blipz_v2.daily_game_attempts(user_id,challenge_id);

CREATE FUNCTION blipz_v2_private.guard_immutable() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog AS $$ BEGIN
  RAISE EXCEPTION USING ERRCODE='P0001', MESSAGE='Immutable V2 record; create a new revision instead';
END $$;
CREATE TRIGGER v2_revision_immutable BEFORE UPDATE OR DELETE ON blipz_v2.game_content_revisions FOR EACH ROW EXECUTE FUNCTION blipz_v2_private.guard_immutable();
CREATE TRIGGER v2_solution_immutable BEFORE UPDATE OR DELETE ON blipz_v2_private.game_solutions FOR EACH ROW EXECUTE FUNCTION blipz_v2_private.guard_immutable();
CREATE TRIGGER v2_activity_immutable BEFORE UPDATE OR DELETE ON blipz_v2.daily_activity FOR EACH ROW EXECUTE FUNCTION blipz_v2_private.guard_immutable();

CREATE FUNCTION blipz_v2_private.guard_challenge() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog AS $$ BEGIN
  IF OLD.status='published' THEN RAISE EXCEPTION 'Published challenge is immutable'; END IF;
  IF TG_OP='DELETE' THEN RETURN OLD; END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER v2_challenge_immutable BEFORE UPDATE OR DELETE ON blipz_v2.daily_challenges FOR EACH ROW EXECUTE FUNCTION blipz_v2_private.guard_challenge();
CREATE FUNCTION blipz_v2_private.guard_instance() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog AS $$ BEGIN
  IF TG_OP<>'INSERT' AND EXISTS(SELECT 1 FROM blipz_v2.daily_challenges WHERE id=OLD.challenge_id AND status='published') THEN RAISE EXCEPTION 'Published instance is immutable'; END IF;
  IF TG_OP<>'DELETE' AND EXISTS(SELECT 1 FROM blipz_v2.daily_challenges WHERE id=NEW.challenge_id AND status='published') THEN RAISE EXCEPTION 'Cannot change a published lineup'; END IF;
  IF TG_OP='DELETE' THEN RETURN OLD; END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER v2_instance_immutable BEFORE INSERT OR UPDATE OR DELETE ON blipz_v2.daily_game_instances FOR EACH ROW EXECUTE FUNCTION blipz_v2_private.guard_instance();
CREATE FUNCTION blipz_v2_private.check_lineup() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog AS $$
DECLARE c blipz_v2.daily_challenges%ROWTYPE; kinds text[]; positions integer[]; points integer;
BEGIN
  SELECT * INTO c FROM blipz_v2.daily_challenges WHERE id=NEW.id;
  IF NOT FOUND OR c.status<>'published' THEN RETURN NULL; END IF;
  SELECT array_agg(game_type ORDER BY position),array_agg(position ORDER BY position),sum(max_points_units) INTO kinds,positions,points FROM blipz_v2.daily_game_instances WHERE challenge_id=c.id;
  IF kinds IS DISTINCT FROM ARRAY['hidden_rule','riddle','trivia','world_guess'] OR positions IS DISTINCT FROM ARRAY[1,2,3,4] OR points IS DISTINCT FROM c.max_points_units THEN RAISE EXCEPTION 'Published lineup is incomplete or invalid'; END IF;
  IF EXISTS (SELECT 1 FROM blipz_v2.daily_game_instances i JOIN blipz_v2.game_content_revisions r ON r.id=i.content_revision_id LEFT JOIN blipz_v2_private.game_solutions s ON s.content_revision_id=r.id
      WHERE i.challenge_id=c.id AND (r.status<>'approved' OR s.content_revision_id IS NULL OR s.solution_payload->>'kind' IS DISTINCT FROM i.game_type OR (i.game_type<>'trivia' AND cardinality(s.normalized_aliases)=0))) THEN RAISE EXCEPTION 'Published lineup requires approved exact solutions'; END IF;
  RETURN NULL;
END $$;
CREATE CONSTRAINT TRIGGER v2_lineup_complete AFTER INSERT OR UPDATE ON blipz_v2.daily_challenges DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION blipz_v2_private.check_lineup();

CREATE FUNCTION blipz_v2_private.guard_attempt() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog AS $$ BEGIN
  IF TG_OP='DELETE' THEN RAISE EXCEPTION 'Attempt history cannot be deleted'; END IF;
  IF OLD.completed_at IS NOT NULL THEN RAISE EXCEPTION 'Final attempt is immutable'; END IF;
  IF ROW(NEW.id,NEW.user_id,NEW.challenge_id,NEW.game_instance_id,NEW.content_revision_id,NEW.game_type,NEW.challenge_date,NEW.started_at,NEW.max_points_units,NEW.scoring_version,NEW.clock_source,NEW.timing_version,NEW.grading_version)
     IS DISTINCT FROM ROW(OLD.id,OLD.user_id,OLD.challenge_id,OLD.game_instance_id,OLD.content_revision_id,OLD.game_type,OLD.challenge_date,OLD.started_at,OLD.max_points_units,OLD.scoring_version,OLD.clock_source,OLD.timing_version,OLD.grading_version) THEN RAISE EXCEPTION 'Attempt identity/timer cannot change'; END IF;
  IF OLD.choices_revealed AND (NOT NEW.choices_revealed OR NEW.choices_revealed_at IS DISTINCT FROM OLD.choices_revealed_at) THEN RAISE EXCEPTION 'Choice reveal is irreversible'; END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER v2_attempt_immutable BEFORE UPDATE OR DELETE ON blipz_v2.daily_game_attempts FOR EACH ROW EXECUTE FUNCTION blipz_v2_private.guard_attempt();
CREATE FUNCTION blipz_v2_private.guard_result() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog AS $$ BEGIN
  IF TG_OP='DELETE' OR OLD.overall_eligible THEN RAISE EXCEPTION 'Final result/history is immutable'; END IF;
  IF ROW(NEW.id,NEW.user_id,NEW.challenge_id,NEW.date,NEW.product_version,NEW.scoring_version,NEW.required_game_count,NEW.max_points_units) IS DISTINCT FROM ROW(OLD.id,OLD.user_id,OLD.challenge_id,OLD.date,OLD.product_version,OLD.scoring_version,OLD.required_game_count,OLD.max_points_units) THEN RAISE EXCEPTION 'Result identity cannot change'; END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER v2_result_immutable BEFORE UPDATE OR DELETE ON blipz_v2.daily_results FOR EACH ROW EXECUTE FUNCTION blipz_v2_private.guard_result();
CREATE FUNCTION blipz_v2_private.check_result() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog AS $$
DECLARE r blipz_v2.daily_results%ROWTYPE; n integer; points integer; elapsed bigint; last_time timestamptz;
BEGIN
  SELECT * INTO r FROM blipz_v2.daily_results WHERE user_id=NEW.user_id AND challenge_id=NEW.challenge_id;
  IF NOT FOUND THEN RAISE EXCEPTION 'Attempt requires daily result'; END IF;
  SELECT count(*),coalesce(sum(awarded_points_units),0),coalesce(sum(elapsed_ms),0),max(completed_at) INTO n,points,elapsed,last_time FROM blipz_v2.daily_game_attempts WHERE user_id=r.user_id AND challenge_id=r.challenge_id AND completed_at IS NOT NULL;
  IF r.completed_game_count<>n OR r.total_points_units<>points OR r.total_elapsed_ms<>elapsed OR (r.overall_eligible AND r.completed_at IS DISTINCT FROM last_time) THEN RAISE EXCEPTION 'Daily result must equal terminal contributions'; END IF;
  RETURN NULL;
END $$;
CREATE CONSTRAINT TRIGGER v2_result_derived AFTER INSERT OR UPDATE ON blipz_v2.daily_results DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION blipz_v2_private.check_result();
CREATE CONSTRAINT TRIGGER v2_attempt_result_derived AFTER INSERT OR UPDATE ON blipz_v2.daily_game_attempts DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION blipz_v2_private.check_result();

DO $$ DECLARE t record; BEGIN
  FOR t IN SELECT schemaname,tablename FROM pg_tables WHERE schemaname IN ('blipz_v2','blipz_v2_private') LOOP
    EXECUTE format('ALTER TABLE %I.%I ENABLE ROW LEVEL SECURITY',t.schemaname,t.tablename);
    EXECUTE format('REVOKE ALL ON TABLE %I.%I FROM PUBLIC, anon, authenticated, service_role',t.schemaname,t.tablename);
    IF t.schemaname='blipz_v2' THEN EXECUTE format('GRANT SELECT ON TABLE %I.%I TO service_role',t.schemaname,t.tablename); END IF;
  END LOOP;
END $$;
REVOKE ALL ON ALL FUNCTIONS IN SCHEMA blipz_v2_private FROM PUBLIC, anon, authenticated, service_role;
COMMIT;
