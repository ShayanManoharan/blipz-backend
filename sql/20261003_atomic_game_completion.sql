-- Atomically complete one daily game and advance the user's streak exactly once.
--
-- Review checkpoint: this migration is intentionally additive and must not be
-- applied until it has been reviewed and explicitly approved.

BEGIN;

CREATE OR REPLACE FUNCTION public.complete_game_attempt_atomic(
    p_user_id UUID,
    p_date DATE,
    p_game TEXT,
    p_score NUMERIC,
    p_maths_elapsed_seconds DOUBLE PRECISION DEFAULT NULL,
    p_guess_text TEXT DEFAULT NULL,
    p_trivia_answers JSONB DEFAULT NULL
)
RETURNS TABLE (
    score_row JSONB,
    already_completed BOOLEAN,
    streak_updated BOOLEAN
)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog
AS $$
DECLARE
    v_user public.users%ROWTYPE;
    v_score public.scores%ROWTYPE;
    v_first_completion BOOLEAN;
    v_played_yesterday BOOLEAN;
    v_new_streak INTEGER;
    v_maths_points NUMERIC := 0;
    v_total NUMERIC;
    v_elapsed NUMERIC;
BEGIN
    IF p_user_id IS NULL OR p_date IS NULL THEN
        RAISE EXCEPTION USING ERRCODE = '22004', MESSAGE = 'user_id and date are required';
    END IF;

    IF p_game NOT IN ('maths', 'guess', 'trivia') THEN
        RAISE EXCEPTION USING ERRCODE = '22023', MESSAGE = 'game must be maths, guess, or trivia';
    END IF;

    IF p_score IS NULL THEN
        RAISE EXCEPTION USING ERRCODE = '22004', MESSAGE = 'score is required';
    END IF;

    IF p_game = 'maths' AND (
        p_score <> pg_catalog.trunc(p_score)
        OR p_score < 0
        OR p_score > 20
        OR p_maths_elapsed_seconds IS NULL
        OR p_maths_elapsed_seconds < 1
    ) THEN
        RAISE EXCEPTION USING ERRCODE = '22023', MESSAGE = 'invalid maths score or elapsed time';
    ELSIF p_game = 'guess' AND (
        p_score < 0
        OR p_score > 10
        OR p_guess_text IS NULL
        OR pg_catalog.btrim(p_guess_text) = ''
    ) THEN
        RAISE EXCEPTION USING ERRCODE = '22023', MESSAGE = 'invalid Guess score or text';
    ELSIF p_game = 'trivia' AND (
        p_score <> pg_catalog.trunc(p_score)
        OR p_score < 0
        OR p_score > 5
        OR p_trivia_answers IS NULL
        OR pg_catalog.jsonb_typeof(p_trivia_answers) <> 'array'
    ) THEN
        RAISE EXCEPTION USING ERRCODE = '22023', MESSAGE = 'invalid trivia score or answers';
    END IF;

    -- Every completion for a user takes locks in the same order. The user lock
    -- serializes concurrent dates/games; the score lock protects the completion flags.
    SELECT u.*
      INTO v_user
      FROM public.users AS u
     WHERE u.id = p_user_id
     FOR UPDATE;

    IF NOT FOUND THEN
        RAISE EXCEPTION USING ERRCODE = 'P0002', MESSAGE = 'user not found';
    END IF;

    INSERT INTO public.scores (user_id, date)
    VALUES (p_user_id, p_date)
    ON CONFLICT (user_id, date) DO NOTHING;

    SELECT s.*
      INTO v_score
      FROM public.scores AS s
     WHERE s.user_id = p_user_id
       AND s.date = p_date
     FOR UPDATE;

    IF NOT FOUND THEN
        RAISE EXCEPTION USING ERRCODE = 'P0002', MESSAGE = 'score row could not be locked';
    END IF;

    IF (p_game = 'maths' AND v_score.maths_completed)
       OR (p_game = 'guess' AND v_score.guess_completed)
       OR (p_game = 'trivia' AND v_score.trivia_completed) THEN
        RETURN QUERY SELECT pg_catalog.to_jsonb(v_score), TRUE, FALSE;
        RETURN;
    END IF;

    v_first_completion := NOT (
        v_score.maths_completed OR v_score.guess_completed OR v_score.trivia_completed
    );

    UPDATE public.scores AS s
       SET maths_score = CASE WHEN p_game = 'maths' THEN p_score::INTEGER ELSE s.maths_score END,
           trivia_score = CASE WHEN p_game = 'trivia' THEN p_score::INTEGER ELSE s.trivia_score END,
           guess_score = CASE WHEN p_game = 'guess' THEN p_score ELSE s.guess_score END,
           maths_completed = s.maths_completed OR p_game = 'maths',
           guess_completed = s.guess_completed OR p_game = 'guess',
           trivia_completed = s.trivia_completed OR p_game = 'trivia',
           maths_elapsed_seconds = CASE
               WHEN p_game = 'maths' THEN p_maths_elapsed_seconds
               ELSE s.maths_elapsed_seconds
           END,
           guess_text = CASE WHEN p_game = 'guess' THEN p_guess_text ELSE s.guess_text END,
           guess_status = CASE WHEN p_game = 'guess' THEN 'completed' ELSE s.guess_status END,
           trivia_answers = CASE
               WHEN p_game = 'trivia' THEN p_trivia_answers
               ELSE s.trivia_answers
           END
     WHERE s.id = v_score.id
     RETURNING s.* INTO v_score;

    -- Preserve the pre-2026-08-06 raw-sum formula for legacy-dated rows. No historical
    -- row is rewritten; this branch only applies if a future call completes such a row.
    IF p_date < DATE '2026-08-06' THEN
        v_total := pg_catalog.round(
            COALESCE(v_score.maths_score, 0)::NUMERIC
            + COALESCE(v_score.trivia_score, 0)::NUMERIC
            + COALESCE(v_score.guess_score, 0)::NUMERIC,
            1
        );
    ELSE
        IF v_score.maths_elapsed_seconds IS NOT NULL THEN
            v_elapsed := v_score.maths_elapsed_seconds::NUMERIC;
            IF v_elapsed <= 25 THEN
                v_maths_points := 30;
            ELSIF v_elapsed <= 30 THEN
                v_maths_points := 30 + ((v_elapsed - 25) / 5) * (28 - 30);
            ELSIF v_elapsed <= 40 THEN
                v_maths_points := 28 + ((v_elapsed - 30) / 10) * (25 - 28);
            ELSIF v_elapsed <= 50 THEN
                v_maths_points := 25 + ((v_elapsed - 40) / 10) * (22 - 25);
            ELSIF v_elapsed <= 60 THEN
                v_maths_points := 22 + ((v_elapsed - 50) / 10) * (19 - 22);
            ELSIF v_elapsed <= 75 THEN
                v_maths_points := 19 + ((v_elapsed - 60) / 15) * (15 - 19);
            ELSIF v_elapsed <= 90 THEN
                v_maths_points := 15 + ((v_elapsed - 75) / 15) * (11 - 15);
            ELSIF v_elapsed <= 120 THEN
                v_maths_points := 11 + ((v_elapsed - 90) / 30) * (6 - 11);
            ELSIF v_elapsed < 150 THEN
                v_maths_points := 6 + ((v_elapsed - 120) / 30) * (3 - 6);
            ELSE
                v_maths_points := 3;
            END IF;
        END IF;

        v_total := pg_catalog.round(
            (COALESCE(v_score.guess_score, 0)::NUMERIC / 10) * 40
            + (COALESCE(v_score.trivia_score, 0)::NUMERIC / 5) * 30
            + v_maths_points,
            1
        );
    END IF;

    UPDATE public.scores AS s
       SET total_score = v_total
     WHERE s.id = v_score.id
     RETURNING s.* INTO v_score;

    IF v_first_completion THEN
        SELECT EXISTS (
            SELECT 1
              FROM public.scores AS yesterday
             WHERE yesterday.user_id = p_user_id
               AND yesterday.date = p_date - 1
               AND (
                   yesterday.maths_completed
                   OR yesterday.guess_completed
                   OR yesterday.trivia_completed
               )
        ) INTO v_played_yesterday;

        v_new_streak := CASE
            WHEN v_played_yesterday THEN COALESCE(v_user.current_streak, 0) + 1
            ELSE 1
        END;

        UPDATE public.users AS u
           SET current_streak = v_new_streak,
               longest_streak = GREATEST(
                   COALESCE(u.longest_streak, 0),
                   v_new_streak
               )
         WHERE u.id = p_user_id;
    END IF;

    RETURN QUERY SELECT pg_catalog.to_jsonb(v_score), FALSE, v_first_completion;
END;
$$;

REVOKE ALL ON FUNCTION public.complete_game_attempt_atomic(
    UUID, DATE, TEXT, NUMERIC, DOUBLE PRECISION, TEXT, JSONB
) FROM PUBLIC;
REVOKE ALL ON FUNCTION public.complete_game_attempt_atomic(
    UUID, DATE, TEXT, NUMERIC, DOUBLE PRECISION, TEXT, JSONB
) FROM anon;
REVOKE ALL ON FUNCTION public.complete_game_attempt_atomic(
    UUID, DATE, TEXT, NUMERIC, DOUBLE PRECISION, TEXT, JSONB
) FROM authenticated;
GRANT EXECUTE ON FUNCTION public.complete_game_attempt_atomic(
    UUID, DATE, TEXT, NUMERIC, DOUBLE PRECISION, TEXT, JSONB
) TO service_role;

COMMIT;
