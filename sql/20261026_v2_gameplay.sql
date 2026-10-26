-- LOCAL/ISOLATED REVIEW ONLY until a separate shared-database application approval.
-- Lock order for all gameplay: public.users -> V2 daily result -> V2 attempt.
BEGIN;
-- SQL CHECK accepts UNKNOWN: explicitly reject absent/null answer-kind fields.
ALTER TABLE blipz_v2.daily_game_attempts ADD CONSTRAINT v2_final_answer_kind_required
  CHECK (final_answer IS NULL OR coalesce(final_answer->>'kind'=CASE answer_mode WHEN 'typed' THEN 'typed' WHEN 'assisted_mcq' THEN 'choice' WHEN 'native_mcq' THEN 'trivia' END,false));
CREATE FUNCTION blipz_v2_private.utc_text(timestamptz) RETURNS text
LANGUAGE sql IMMUTABLE STRICT SET search_path=pg_catalog AS $$
 SELECT to_char($1 AT TIME ZONE 'UTC','YYYY-MM-DD"T"HH24:MI:SS.US"Z"')
$$;
CREATE FUNCTION blipz_v2_private.attempt_json(a blipz_v2.daily_game_attempts) RETURNS jsonb
LANGUAGE sql STABLE SET search_path=pg_catalog AS $$
 SELECT to_jsonb(a)||jsonb_build_object('started_at',blipz_v2_private.utc_text(a.started_at),'choices_revealed_at',blipz_v2_private.utc_text(a.choices_revealed_at),'completed_at',blipz_v2_private.utc_text(a.completed_at))
$$;
CREATE FUNCTION blipz_v2_private.result_json(p_user uuid,p_challenge uuid) RETURNS jsonb
LANGUAGE sql STABLE SET search_path=pg_catalog AS $$
 SELECT to_jsonb(r)||jsonb_build_object('completed_at',blipz_v2_private.utc_text(r.completed_at),'games',coalesce((SELECT jsonb_agg(jsonb_build_object(
  'game_instance_id',a.game_instance_id,'attempt_id',a.id,'game_type',a.game_type,'state',a.state,
  'awarded_points_units',a.awarded_points_units,'max_points_units',a.max_points_units,'elapsed_ms',a.elapsed_ms,
  'answer_mode',a.answer_mode,'accuracy_bps',a.accuracy_bps,'score_percentage_bps',a.score_percentage_bps) ORDER BY i.position)
  FROM blipz_v2.daily_game_attempts a JOIN blipz_v2.daily_game_instances i ON i.id=a.game_instance_id
  WHERE a.user_id=p_user AND a.challenge_id=p_challenge AND a.completed_at IS NOT NULL),'[]'::jsonb))
 FROM blipz_v2.daily_results r WHERE r.user_id=p_user AND r.challenge_id=p_challenge
$$;
CREATE FUNCTION blipz_v2_private.lock_context(p_user uuid,p_instance uuid) RETURNS blipz_v2.daily_game_instances
LANGUAGE plpgsql SET search_path=pg_catalog AS $$
DECLARE i blipz_v2.daily_game_instances%ROWTYPE;
BEGIN
  IF p_user IS NULL OR p_instance IS NULL THEN RAISE EXCEPTION 'user and instance are required'; END IF;
  PERFORM 1 FROM public.users WHERE id=p_user FOR UPDATE;
  IF NOT FOUND THEN RAISE EXCEPTION USING ERRCODE='P0002',MESSAGE='user_not_found'; END IF;
  SELECT x.* INTO i FROM blipz_v2.daily_game_instances x JOIN blipz_v2.daily_challenges c ON c.id=x.challenge_id WHERE x.id=p_instance AND c.status='published';
  IF NOT FOUND THEN RAISE EXCEPTION USING ERRCODE='P0002',MESSAGE='instance_not_found'; END IF;
  PERFORM 1 FROM blipz_v2.daily_results WHERE user_id=p_user AND challenge_id=i.challenge_id FOR UPDATE;
  RETURN i;
END $$;
CREATE FUNCTION blipz_v2_private.assert_open(c blipz_v2.daily_challenges) RETURNS timestamptz
LANGUAGE plpgsql VOLATILE SET search_path=pg_catalog AS $$
DECLARE t timestamptz:=clock_timestamp();
BEGIN
  IF t<c.opens_at THEN RAISE EXCEPTION 'challenge_not_open'; END IF;
  IF t>=c.closes_at THEN RAISE EXCEPTION 'challenge_closed'; END IF;
  RETURN t;
END $$;
CREATE FUNCTION blipz_v2_private.assert_no_v1_competition(p_date date) RETURNS void
LANGUAGE plpgsql SET search_path=pg_catalog AS $$ BEGIN
  IF EXISTS(SELECT 1 FROM public.scores WHERE date=p_date AND (guess_completed OR maths_completed OR trivia_completed)) THEN
    RAISE EXCEPTION 'competitive_version_conflict';
  END IF;
END $$;

CREATE FUNCTION blipz_v2.start_game_attempt(p_user_id uuid,p_game_instance_id uuid,p_idempotency_key uuid) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE i blipz_v2.daily_game_instances%ROWTYPE; c blipz_v2.daily_challenges%ROWTYPE; a blipz_v2.daily_game_attempts%ROWTYPE;
 r blipz_v2.game_content_revisions%ROWTYPE; t timestamptz; existed boolean;
BEGIN
  IF p_idempotency_key IS NULL THEN RAISE EXCEPTION 'idempotency_key_required'; END IF;
  i:=blipz_v2_private.lock_context(p_user_id,p_game_instance_id);
  SELECT * INTO c FROM blipz_v2.daily_challenges WHERE id=i.challenge_id;
  PERFORM blipz_v2_private.assert_open(c);
  PERFORM blipz_v2_private.assert_no_v1_competition(c.date);
  INSERT INTO blipz_v2.daily_results(user_id,challenge_id,date) VALUES(p_user_id,c.id,c.date) ON CONFLICT(user_id,challenge_id) DO NOTHING;
  PERFORM 1 FROM blipz_v2.daily_results WHERE user_id=p_user_id AND challenge_id=c.id FOR UPDATE;
  SELECT * INTO a FROM blipz_v2.daily_game_attempts WHERE user_id=p_user_id AND game_instance_id=i.id FOR UPDATE;
  existed:=FOUND;
  t:=blipz_v2_private.assert_open(c);
  IF NOT existed THEN
    INSERT INTO blipz_v2.daily_game_attempts(user_id,challenge_id,game_instance_id,content_revision_id,game_type,challenge_date,started_at,max_points_units)
    VALUES(p_user_id,c.id,i.id,i.content_revision_id,i.game_type,c.date,t,i.max_points_units) RETURNING * INTO a;
  END IF;
  SELECT * INTO r FROM blipz_v2.game_content_revisions WHERE id=i.content_revision_id;
  RETURN jsonb_build_object('attempt',blipz_v2_private.attempt_json(a),'content',jsonb_build_object('id',r.id,'game_type',r.game_type,'variant',r.variant,'schema_version',r.schema_version,'public_payload',r.public_payload),
    'server_time',blipz_v2_private.utc_text(t),'closes_at',blipz_v2_private.utc_text(c.closes_at),'already_started',existed,'already_completed',a.completed_at IS NOT NULL);
END $$;

CREATE FUNCTION blipz_v2.reveal_game_choices(p_user_id uuid,p_game_instance_id uuid,p_idempotency_key uuid) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE i blipz_v2.daily_game_instances%ROWTYPE; c blipz_v2.daily_challenges%ROWTYPE; a blipz_v2.daily_game_attempts%ROWTYPE;
 s blipz_v2_private.game_solutions%ROWTYPE; choices jsonb; existed boolean; t timestamptz;
BEGIN
  IF p_idempotency_key IS NULL THEN RAISE EXCEPTION 'idempotency_key_required'; END IF;
  i:=blipz_v2_private.lock_context(p_user_id,p_game_instance_id);
  SELECT * INTO a FROM blipz_v2.daily_game_attempts WHERE user_id=p_user_id AND game_instance_id=i.id FOR UPDATE;
  IF NOT FOUND THEN RAISE EXCEPTION 'attempt_not_started'; END IF;
  IF a.completed_at IS NOT NULL THEN RAISE EXCEPTION 'attempt_completed'; END IF;
  IF i.game_type='trivia' THEN RAISE EXCEPTION 'choices_not_supported'; END IF;
  SELECT * INTO c FROM blipz_v2.daily_challenges WHERE id=i.challenge_id;
  t:=blipz_v2_private.assert_open(c);
  PERFORM blipz_v2_private.assert_no_v1_competition(c.date);
  SELECT * INTO s FROM blipz_v2_private.game_solutions WHERE content_revision_id=a.content_revision_id;
  SELECT jsonb_agg(jsonb_build_object('id',x->>'id','text',x->>'text') ORDER BY ord) INTO choices FROM jsonb_array_elements(s.solution_payload->'choices') WITH ORDINALITY AS e(x,ord);
  IF jsonb_array_length(choices) IS DISTINCT FROM 4 OR (SELECT count(DISTINCT x->>'id') FROM jsonb_array_elements(choices) x WHERE x->>'id' IN ('A','B','C','D'))<>4 THEN RAISE EXCEPTION 'invalid_approved_choices'; END IF;
  existed:=a.choices_revealed;
  IF NOT existed THEN
    t:=blipz_v2_private.assert_open(c);
    UPDATE blipz_v2.daily_game_attempts SET state='choices_revealed',choices_revealed=true,choices_revealed_at=t,available_points_units=1875 WHERE id=a.id RETURNING * INTO a;
  END IF;
  RETURN jsonb_build_object('attempt_id',a.id,'game_instance_id',i.id,'choices',choices,'choices_revealed_at',blipz_v2_private.utc_text(a.choices_revealed_at),'available_points_units',a.available_points_units,'already_revealed',existed);
END $$;

CREATE FUNCTION blipz_v2.submit_game_answer(p_user_id uuid,p_game_instance_id uuid,p_idempotency_key uuid,p_answer jsonb,p_normalized_text text DEFAULT NULL) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE i blipz_v2.daily_game_instances%ROWTYPE; c blipz_v2.daily_challenges%ROWTYPE; a blipz_v2.daily_game_attempts%ROWTYPE;
 s blipz_v2_private.game_solutions%ROWTYPE; u public.users%ROWTYPE; public_data jsonb; q jsonb; selected text; correct_id text;
 t timestamptz; duration bigint; correct integer:=0; total integer:=1; points integer; mode text;
 already boolean:=false; activity_created boolean:=false; next_streak integer; n integer; sum_points integer; sum_time bigint; last_time timestamptz;
BEGIN
  IF p_idempotency_key IS NULL THEN RAISE EXCEPTION 'idempotency_key_required'; END IF;
  i:=blipz_v2_private.lock_context(p_user_id,p_game_instance_id);
  SELECT * INTO a FROM blipz_v2.daily_game_attempts WHERE user_id=p_user_id AND game_instance_id=i.id FOR UPDATE;
  IF NOT FOUND THEN RAISE EXCEPTION 'attempt_not_started'; END IF;
  IF a.completed_at IS NOT NULL THEN
    IF p_idempotency_key=a.completion_idempotency_key AND p_answer IS DISTINCT FROM a.final_answer THEN RAISE EXCEPTION 'idempotency_key_reused'; END IF;
    already:=true;
  ELSE
    SELECT * INTO c FROM blipz_v2.daily_challenges WHERE id=i.challenge_id;
    PERFORM blipz_v2_private.assert_open(c);
    PERFORM blipz_v2_private.assert_no_v1_competition(c.date);
    SELECT * INTO s FROM blipz_v2_private.game_solutions WHERE content_revision_id=a.content_revision_id;
    SELECT public_payload INTO public_data FROM blipz_v2.game_content_revisions WHERE id=a.content_revision_id;
    IF s.content_revision_id IS NULL OR jsonb_typeof(p_answer) IS DISTINCT FROM 'object' THEN RAISE EXCEPTION 'invalid_answer'; END IF;
    IF i.game_type='trivia' THEN
      mode:='native_mcq'; total:=5;
      IF p_normalized_text IS NOT NULL OR p_answer->>'kind' IS DISTINCT FROM 'trivia' OR p_answer-ARRAY['kind','answers']<>'{}'::jsonb OR jsonb_typeof(p_answer->'answers') IS DISTINCT FROM 'array' THEN RAISE EXCEPTION 'answer_mode_mismatch'; END IF;
      IF jsonb_array_length(p_answer->'answers')<>5 OR jsonb_array_length(public_data->'questions') IS DISTINCT FROM 5 OR jsonb_array_length(s.solution_payload->'questions') IS DISTINCT FROM 5 THEN RAISE EXCEPTION 'invalid_trivia_answers'; END IF;
      IF (SELECT count(DISTINCT x->>'question_id') FROM jsonb_array_elements(p_answer->'answers') x)<>5 OR EXISTS(
        SELECT 1 FROM jsonb_array_elements(p_answer->'answers') x WHERE jsonb_typeof(x) IS DISTINCT FROM 'object' OR x-ARRAY['question_id','selected_option_id']<>'{}'::jsonb
        OR jsonb_typeof(x->'question_id') IS DISTINCT FROM 'string' OR coalesce(x->>'selected_option_id','') NOT IN ('A','B','C','D')
        OR NOT EXISTS(SELECT 1 FROM jsonb_array_elements(public_data->'questions') p WHERE p->>'id'=x->>'question_id')) THEN RAISE EXCEPTION 'invalid_trivia_answers'; END IF;
      FOR q IN SELECT x FROM jsonb_array_elements(public_data->'questions') x LOOP
        SELECT x->>'selected_option_id' INTO selected FROM jsonb_array_elements(p_answer->'answers') x WHERE x->>'question_id'=q->>'id';
        SELECT x->>'correct_choice_id' INTO correct_id FROM jsonb_array_elements(s.solution_payload->'questions') x WHERE x->>'question_id'=q->>'id';
        IF correct_id IS NULL OR correct_id NOT IN ('A','B','C','D') THEN RAISE EXCEPTION 'invalid_approved_solution'; END IF;
        IF selected=correct_id THEN correct:=correct+1; END IF;
      END LOOP;
      points:=correct*500;
    ELSIF i.game_type IN ('hidden_rule','riddle','world_guess') THEN
      IF a.choices_revealed THEN
        mode:='assisted_mcq';
        IF p_normalized_text IS NOT NULL OR p_answer->>'kind' IS DISTINCT FROM 'choice' OR p_answer-ARRAY['kind','selected_option_id']<>'{}'::jsonb OR coalesce(p_answer->>'selected_option_id','') NOT IN ('A','B','C','D') THEN RAISE EXCEPTION 'answer_mode_mismatch'; END IF;
        correct:=(p_answer->>'selected_option_id'=s.solution_payload->>'correct_choice_id')::integer;
      ELSE
        mode:='typed';
        IF p_answer->>'kind' IS DISTINCT FROM 'typed' OR p_answer-ARRAY['kind','text']<>'{}'::jsonb OR jsonb_typeof(p_answer->'text') IS DISTINCT FROM 'string' OR char_length(p_answer->>'text') NOT BETWEEN 1 AND 300 OR btrim(p_answer->>'text')='' THEN RAISE EXCEPTION 'answer_mode_mismatch'; END IF;
        -- Trusted backend computes normalization v1 and never accepts this argument
        -- from a client. SQL performs the correctness comparison, not the caller.
        IF p_normalized_text IS NULL OR s.normalization_version<>'answer-normalization-v1' THEN RAISE EXCEPTION 'trusted_normalization_required'; END IF;
        correct:=(p_normalized_text=ANY(s.normalized_aliases))::integer;
      END IF;
      points:=CASE WHEN correct=1 THEN a.available_points_units ELSE 0 END;
    ELSE RAISE EXCEPTION 'unsupported_game_type';
    END IF;
    IF correct IS NULL THEN RAISE EXCEPTION 'invalid_approved_solution'; END IF;
    t:=blipz_v2_private.assert_open(c);
    duration:=floor(extract(epoch FROM (t-a.started_at))*1000)::bigint;
    IF duration<0 THEN RAISE EXCEPTION 'database_clock_moved_backwards'; END IF;
    UPDATE blipz_v2.daily_game_attempts SET state=CASE WHEN correct=total THEN 'completed_correct' ELSE 'completed_incorrect' END,
      completed_at=t,elapsed_ms=duration,answer_mode=mode,final_answer=p_answer,correct_count=correct,question_count=total,is_correct=(correct=total),accuracy_bps=correct*10000/total,
      awarded_points_units=points,score_percentage_bps=points*10000/max_points_units,completion_idempotency_key=p_idempotency_key WHERE id=a.id RETURNING * INTO a;
    SELECT count(*),coalesce(sum(awarded_points_units),0),coalesce(sum(elapsed_ms),0),max(completed_at) INTO n,sum_points,sum_time,last_time FROM blipz_v2.daily_game_attempts WHERE user_id=p_user_id AND challenge_id=c.id AND completed_at IS NOT NULL;
    UPDATE blipz_v2.daily_results SET completed_game_count=n,total_points_units=sum_points,total_elapsed_ms=sum_time,state=CASE WHEN n=required_game_count THEN 'completed' ELSE 'in_progress' END,
      overall_eligible=(n=required_game_count),completed_at=CASE WHEN n=required_game_count THEN last_time ELSE NULL END WHERE user_id=p_user_id AND challenge_id=c.id;
    SELECT * INTO u FROM public.users WHERE id=p_user_id;
    IF NOT EXISTS(SELECT 1 FROM blipz_v2.daily_activity WHERE user_id=p_user_id AND date=c.date) THEN
      next_streak:=CASE WHEN EXISTS(SELECT 1 FROM blipz_v2.daily_activity WHERE user_id=p_user_id AND date=c.date-1)
        OR EXISTS(SELECT 1 FROM public.scores WHERE user_id=p_user_id AND date=c.date-1 AND (guess_completed OR maths_completed OR trivia_completed)) THEN coalesce(u.current_streak,0)+1 ELSE 1 END;
      INSERT INTO blipz_v2.daily_activity(user_id,date,first_completed_at,source_product,source_attempt_id,streak_after,longest_streak_after)
      VALUES(p_user_id,c.date,t,'blipz-v2',a.id,next_streak,greatest(coalesce(u.longest_streak,0),next_streak));
      UPDATE public.users SET current_streak=next_streak,longest_streak=greatest(coalesce(longest_streak,0),next_streak) WHERE id=p_user_id;
      activity_created:=true;
    END IF;
  END IF;
  SELECT * INTO u FROM public.users WHERE id=p_user_id;
  RETURN jsonb_build_object('attempt',blipz_v2_private.attempt_json(a),'daily_result',blipz_v2_private.result_json(p_user_id,i.challenge_id),
    'already_completed',already,'review_available',true,'activity_created',activity_created,'current_streak',u.current_streak,'longest_streak',u.longest_streak);
END $$;

REVOKE ALL ON ALL FUNCTIONS IN SCHEMA blipz_v2_private FROM PUBLIC, anon, authenticated, service_role;
REVOKE ALL ON FUNCTION blipz_v2.start_game_attempt(uuid,uuid,uuid),blipz_v2.reveal_game_choices(uuid,uuid,uuid),blipz_v2.submit_game_answer(uuid,uuid,uuid,jsonb,text) FROM PUBLIC, anon, authenticated, service_role;
GRANT EXECUTE ON FUNCTION blipz_v2.start_game_attempt(uuid,uuid,uuid),blipz_v2.reveal_game_choices(uuid,uuid,uuid),blipz_v2.submit_game_answer(uuid,uuid,uuid,jsonb,text) TO service_role;
COMMIT;
