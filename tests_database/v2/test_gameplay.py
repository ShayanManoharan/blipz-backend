import json
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
import time
from uuid import uuid4

import pytest
from app.v2.models import StartAttemptResponse, RevealChoicesResponse, SubmitResponse, TypedFinalAnswer
from app.v2.rpc_inputs import prepare_final_answer
from .local_db import literal


@pytest.fixture(scope='session')
def rpc_db(schema_db):
    schema_db.migrate('20261026_v2_gameplay.sql')
    return schema_db


@pytest.fixture
def game(rpc_db):
    user,games=rpc_db.seed()
    return rpc_db,user,games


def call(db,name,user,instance,answer=None,key=None,*,ok=True):
    key=key or str(uuid4())
    args=[literal(user),literal(instance),literal(key)]
    if name=='submit_game_answer':
        normalized=prepare_final_answer(TypedFinalAnswer.model_validate(answer)).normalized_text if answer.get('kind')=='typed' else None
        args += [literal(answer)+'::jsonb',literal(normalized)]
    expression=f"blipz_v2.{name}({','.join(args)})"
    value=db.sql('SET ROLE service_role; SELECT '+expression+';',ok=ok)
    return json.loads(value) if ok else value


def start(db,user,game):return call(db,'start_game_attempt',user,game['id'])
def reveal(db,user,game,**kwargs):return call(db,'reveal_game_choices',user,game['id'],**kwargs)
def submit(db,user,game,text='divisible by 3',**kwargs):
    answer=kwargs.pop('answer',None) or {'kind':'typed','text':text}
    return call(db,'submit_game_answer',user,game['id'],answer,**kwargs)


def race(db,user,operations):
    """Two independent psql sessions demonstrably wait on the same user lock."""
    gate=Barrier(len(operations))
    def worker(operation):
        gate.wait()
        try:return ('ok',operation())
        except AssertionError as exc:return ('error',str(exc))
    with ThreadPoolExecutor(max_workers=3) as pool:
        holder=pool.submit(db.sql,f"BEGIN; SET application_name='v2-lock-holder'; SELECT id FROM public.users WHERE id={literal(user)} FOR UPDATE; SELECT pg_sleep(3); COMMIT;")
        for _ in range(30):
            if db.value("SELECT count(*) FROM pg_stat_activity WHERE application_name='v2-lock-holder' AND wait_event='PgSleep'")==1:break
            time.sleep(.02)
        else:raise AssertionError('Race lock holder not ready')
        work=[pool.submit(worker,o) for o in operations]
        queued=False
        for _ in range(20):
            if db.value("SELECT count(*) FROM pg_stat_activity WHERE wait_event_type='Lock' AND query LIKE '%blipz_v2.%'")>=len(operations):queued=True;break
            time.sleep(.02)
        assert queued,'Both real PostgreSQL sessions must contend on the lock'
        results=[w.result(timeout=15) for w in work]
        holder.result(timeout=15)
        return results


def test_start_shape_idempotency_and_no_private_data(game):
    db,user,games=game
    first=start(db,user,games[0]);second=start(db,user,games[0])
    StartAttemptResponse.model_validate(first)
    assert first['attempt']==second['attempt'] and second['already_started']
    assert not first['already_completed']
    assert 'private_solution' not in json.dumps(first) and 'correct_choice_id' not in json.dumps(first)
    assert db.value('SELECT count(*) FROM blipz_v2.daily_game_attempts')==1
    assert db.value('SELECT count(*) FROM blipz_v2.daily_activity')==0


def test_typed_correct_and_secure_normalization(game):
    db,user,games=game;start(db,user,games[0])
    result=submit(db,user,games[0],'  DIVISIBLE BY ３! ')
    SubmitResponse.model_validate(result)
    assert result['attempt']['awarded_points_units']==2500
    assert result['attempt']['elapsed_ms']>=0 and result['current_streak']==1
    assert result['activity_created']


def test_wrong_final_zero_is_terminal_and_advances_once(game):
    db,user,games=game;start(db,user,games[0])
    first=submit(db,user,games[0],'wrong')
    assert first['attempt']['state']=='completed_incorrect' and first['attempt']['awarded_points_units']==0
    assert first['current_streak']==1 and first['activity_created']
    assert 'attempt_completed' in reveal(db,user,games[0],ok=False)
    duplicate=submit(db,user,games[0])
    assert duplicate['already_completed'] and duplicate['attempt']==first['attempt'] and not duplicate['activity_created']
    start(db,user,games[1]);second=submit(db,user,games[1],'piano')
    assert second['current_streak']==1 and not second['activity_created']
    assert db.value('SELECT count(*) FROM blipz_v2.daily_activity')==1


@pytest.mark.parametrize('correct',[True,False])
def test_assisted_score_and_reveal_idempotency(game,correct):
    db,user,games=game;start(db,user,games[0])
    first=reveal(db,user,games[0]);second=reveal(db,user,games[0])
    RevealChoicesResponse.model_validate(first)
    assert first['choices']==second['choices'] and first['choices_revealed_at']==second['choices_revealed_at']
    assert second['already_revealed']
    assert all(set(c)=={'id','text'} for c in first['choices'])
    assert 'answer_mode_mismatch' in submit(db,user,games[0],ok=False)
    result=submit(db,user,games[0],answer={'kind':'choice','selected_option_id':'B' if correct else 'A'})
    SubmitResponse.model_validate(result)
    assert result['attempt']['awarded_points_units']==(1875 if correct else 0)


def test_requires_start_and_rejects_choice_before_reveal(game):
    db,user,games=game
    assert 'attempt_not_started' in submit(db,user,games[0],ok=False)
    assert 'attempt_not_started' in reveal(db,user,games[0],ok=False)
    start(db,user,games[0])
    assert 'answer_mode_mismatch' in submit(db,user,games[0],answer={'kind':'choice','selected_option_id':'B'},ok=False)


@pytest.mark.parametrize('correct_count',range(6))
def test_trivia_server_grades_option_ids(game,correct_count):
    db,user,games=game;start(db,user,games[2])
    assert 'choices_not_supported' in reveal(db,user,games[2],ok=False)
    correct_ids=['B','C','B','B','D']
    answer={'kind':'trivia','answers':[{'question_id':f'q{n}','selected_option_id':correct_ids[n] if n<correct_count else 'A'} for n in range(5)]}
    result=submit(db,user,games[2],answer=answer)
    SubmitResponse.model_validate(result)
    assert result['attempt']['correct_count']==correct_count
    assert result['attempt']['awarded_points_units']==correct_count*500


@pytest.mark.parametrize('case',['duplicate_ids','foreign_id','missing','text_instead_of_id','extra_field'])
def test_invalid_trivia_does_not_complete(game,case):
    db,user,games=game;start(db,user,games[2])
    answer={'kind':'trivia','answers':[{'question_id':f'q{n}','selected_option_id':'B'} for n in range(5)]}
    if case=='duplicate_ids':answer['answers'][1]['question_id']='q0'
    elif case=='foreign_id':answer['answers'][0]['question_id']='q99'
    elif case=='missing':answer['answers'].pop()
    elif case=='text_instead_of_id':answer['answers'][0]['selected_option_id']='Mars'
    else:answer['answers'][0]['is_correct']=True
    assert 'invalid_trivia_answers' in submit(db,user,games[2],answer=answer,ok=False)
    assert db.value("SELECT state FROM blipz_v2.daily_game_attempts")=='active'
    assert db.value('SELECT count(*) FROM blipz_v2.daily_activity')==0


def test_same_key_different_answer_rejected_original_unchanged(game):
    db,user,games=game;start(db,user,games[0]);key=str(uuid4())
    first=submit(db,user,games[0],key=key)
    assert 'idempotency_key_reused' in submit(db,user,games[0],'wrong',key=key,ok=False)
    assert submit(db,user,games[0],key=key)['attempt']==first['attempt']


def test_complete_day_total_and_times_are_derived(game):
    db,user,games=game
    start(db,user,games[0]);submit(db,user,games[0])
    start(db,user,games[1]);reveal(db,user,games[1]);submit(db,user,games[1],answer={'kind':'choice','selected_option_id':'B'})
    start(db,user,games[2]);submit(db,user,games[2],answer={'kind':'trivia','answers':[{'question_id':f'q{n}','selected_option_id':c} for n,c in enumerate(['B','C','B','B','A'])]})
    start(db,user,games[3]);final=submit(db,user,games[3],'Japan')
    SubmitResponse.model_validate(final)
    result=final['daily_result']
    assert result['total_points_units']==8875 and result['overall_eligible'] and result['completed_game_count']==4
    assert result['total_elapsed_ms']==sum(g['elapsed_ms'] for g in result['games'])
    assert db.value('SELECT count(*) FROM blipz_v2.daily_activity')==1
    assert final['current_streak']==1
    reopened=start(db,user,games[0]);assert reopened['already_completed'] and reopened['attempt']['state']=='completed_correct'


def test_reveal_vs_typed_submit_race(game):
    db,user,games=game;start(db,user,games[0])
    results=race(db,user,[lambda:reveal(db,user,games[0]),lambda:submit(db,user,games[0])])
    assert sum(status=='ok' for status,_ in results)==1
    errors=' '.join(str(value) for status,value in results if status=='error')
    assert 'attempt_completed' in errors or 'answer_mode_mismatch' in errors
    state=db.value('SELECT state FROM blipz_v2.daily_game_attempts')
    if state=='choices_revealed':
        result=submit(db,user,games[0],answer={'kind':'choice','selected_option_id':'B'})
        assert result['attempt']['awarded_points_units']==1875
    else:assert db.value('SELECT awarded_points_units FROM blipz_v2.daily_game_attempts')==2500


@pytest.mark.parametrize('assisted',[False,True])
def test_duplicate_submissions_race(game,assisted):
    db,user,games=game;start(db,user,games[0])
    if assisted:reveal(db,user,games[0])
    answer={'kind':'choice','selected_option_id':'B'} if assisted else {'kind':'typed','text':'divisible by 3'}
    results=race(db,user,[lambda:submit(db,user,games[0],answer=answer),lambda:submit(db,user,games[0],answer=answer)])
    assert all(status=='ok' for status,_ in results)
    rows=[row for _,row in results]
    assert rows[0]['attempt']==rows[1]['attempt']
    assert sum(row['already_completed'] for row in rows)==1
    assert rows[0]['attempt']['awarded_points_units']==(1875 if assisted else 2500)
    assert db.value('SELECT count(*) FROM blipz_v2.daily_activity')==1


def test_two_simultaneous_starts_never_reset_timer(game):
    db,user,games=game
    results=race(db,user,[lambda:start(db,user,games[0]),lambda:start(db,user,games[0])])
    assert all(status=='ok' for status,_ in results)
    assert results[0][1]['attempt']==results[1][1]['attempt']
    assert sum(r['already_started'] for _,r in results)==1
    assert start(db,user,games[0])['attempt']['started_at']==results[0][1]['attempt']['started_at']


def test_two_game_completions_advance_streak_once(game):
    db,user,games=game;start(db,user,games[0]);start(db,user,games[1])
    results=race(db,user,[lambda:submit(db,user,games[0]),lambda:submit(db,user,games[1],'piano')])
    assert all(status=='ok' for status,_ in results)
    assert sum(r['activity_created'] for _,r in results)==1
    assert db.value(f'SELECT current_streak FROM public.users WHERE id={literal(user)}')==1
    assert db.value('SELECT total_points_units FROM blipz_v2.daily_results')==5000


def test_yesterday_v1_activity_continues_streak_without_rewriting_legacy(game):
    db,user,games=game
    db.sql(f"UPDATE public.users SET current_streak=3,longest_streak=5 WHERE id={literal(user)}; INSERT INTO public.scores(user_id,date,guess_completed) VALUES({literal(user)},(clock_timestamp() AT TIME ZONE 'UTC')::date-1,true);")
    before=db.sql(f'SELECT to_jsonb(s) FROM public.scores s WHERE user_id={literal(user)};')
    start(db,user,games[0]);result=submit(db,user,games[0],'wrong')
    assert result['current_streak']==4
    assert db.sql(f'SELECT to_jsonb(s) FROM public.scores s WHERE user_id={literal(user)};')==before


def test_existing_v1_competition_rejected(game):
    db,user,games=game
    db.sql(f"INSERT INTO public.scores(user_id,date,guess_completed) VALUES({literal(user)},(clock_timestamp() AT TIME ZONE 'UTC')::date,true);")
    assert 'competitive_version_conflict' in call(db,'start_game_attempt',user,games[0]['id'],ok=False)


def test_other_users_v1_competition_also_blocks_v2_day(game):
    db,user,games=game;other=str(uuid4())
    db.sql(f"INSERT INTO public.users(id,username) VALUES({literal(other)},{literal('local_db_'+other)}); INSERT INTO public.scores(user_id,date,trivia_completed) VALUES({literal(other)},(clock_timestamp() AT TIME ZONE 'UTC')::date,true);")
    assert 'competitive_version_conflict' in call(db,'start_game_attempt',user,games[0]['id'],ok=False)


@pytest.mark.parametrize('operation',['start_game_attempt','reveal_game_choices','submit_game_answer'])
def test_closed_challenge_rejects_unfinished_mutation(rpc_db,operation):
    db=rpc_db;user,games=db.seed(day_offset=-1)
    i=games[0]
    db.sql(f"BEGIN; INSERT INTO blipz_v2.daily_results(user_id,challenge_id,date) SELECT {literal(user)},id,date FROM blipz_v2.daily_challenges; INSERT INTO blipz_v2.daily_game_attempts(user_id,challenge_id,game_instance_id,content_revision_id,game_type,challenge_date,started_at,max_points_units) SELECT {literal(user)},i.challenge_id,i.id,i.content_revision_id,i.game_type,c.date,c.opens_at+interval '12 hours',i.max_points_units FROM blipz_v2.daily_game_instances i JOIN blipz_v2.daily_challenges c ON c.id=i.challenge_id WHERE i.id={literal(i['id'])}; COMMIT;")
    assert 'challenge_closed' in call(db,operation,user,i['id'],answer={'kind':'typed','text':'divisible by 3'},ok=False)
    assert db.value('SELECT completed_game_count FROM blipz_v2.daily_results')==0


def test_atomic_rollback_if_deferred_activity_write_fails(game):
    db,user,games=game;before=start(db,user,games[0])['attempt']
    db.sql("CREATE FUNCTION blipz_v2_private.test_fail() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'forced_activity_failure'; END $$; CREATE CONSTRAINT TRIGGER test_fail AFTER INSERT ON blipz_v2.daily_activity DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION blipz_v2_private.test_fail();")
    try:
        assert 'forced_activity_failure' in submit(db,user,games[0],ok=False)
        assert start(db,user,games[0])['attempt']==before
        assert db.value('SELECT total_points_units FROM blipz_v2.daily_results')==0
        assert db.value('SELECT count(*) FROM blipz_v2.daily_activity')==0
        assert db.value(f'SELECT current_streak FROM public.users WHERE id={literal(user)}')==0
    finally:db.sql('DROP TRIGGER test_fail ON blipz_v2.daily_activity; DROP FUNCTION blipz_v2_private.test_fail();')


def test_rpc_privileges_and_safe_search_path(rpc_db):
    db=rpc_db
    assert db.value("SELECT bool_and(p.prosecdef AND p.proconfig=ARRAY['search_path=pg_catalog']) FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace WHERE n.nspname='blipz_v2'") is True
    for role in ['anon','authenticated']:
        assert db.value(f"SELECT bool_or(has_function_privilege('{role}',p.oid,'EXECUTE')) FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace WHERE n.nspname='blipz_v2'") is False
    assert db.value("SELECT bool_and(has_function_privilege('service_role',p.oid,'EXECUTE')) FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace WHERE n.nspname='blipz_v2'") is True
    assert db.value("SELECT count(*) FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace CROSS JOIN LATERAL aclexplode(p.proacl) a WHERE n.nspname IN ('blipz_v2','blipz_v2_private') AND a.grantee=0")==0
    assert db.v1_snapshot()==db.baseline


def test_retry_after_midnight_returns_unchanged_terminal_attempt(rpc_db):
    db=rpc_db;user,games=db.seed(day_offset=-1);i=games[0];key=str(uuid4())
    db.sql(f"BEGIN; INSERT INTO blipz_v2.daily_results(user_id,challenge_id,date) SELECT {literal(user)},id,date FROM blipz_v2.daily_challenges; INSERT INTO blipz_v2.daily_game_attempts(user_id,challenge_id,game_instance_id,content_revision_id,game_type,challenge_date,started_at,max_points_units,state,completed_at,elapsed_ms,answer_mode,final_answer,correct_count,question_count,is_correct,accuracy_bps,awarded_points_units,score_percentage_bps,completion_idempotency_key) SELECT {literal(user)},i.challenge_id,i.id,i.content_revision_id,i.game_type,c.date,c.opens_at+interval '12 hours',i.max_points_units,'completed_incorrect',c.opens_at+interval '12 hours 1 second',1000,'typed','{{\"kind\":\"typed\",\"text\":\"wrong\"}}',0,1,false,0,0,0,{literal(key)} FROM blipz_v2.daily_game_instances i JOIN blipz_v2.daily_challenges c ON c.id=i.challenge_id WHERE i.id={literal(i['id'])}; UPDATE blipz_v2.daily_results SET completed_game_count=1,total_elapsed_ms=1000 WHERE user_id={literal(user)}; COMMIT;")
    result=submit(db,user,i,'wrong',key=key)
    SubmitResponse.model_validate(result)
    assert result['already_completed'] and result['attempt']['awarded_points_units']==0 and not result['activity_created']
    assert 'idempotency_key_reused' in submit(db,user,i,key=key,ok=False)


def test_hostile_caller_search_path_cannot_replace_clock(game):
    db,user,games=game
    db.sql("CREATE SCHEMA caller_controlled; CREATE FUNCTION caller_controlled.clock_timestamp() RETURNS timestamptz LANGUAGE sql AS $$ SELECT '2000-01-01T00:00:00Z'::timestamptz $$; GRANT USAGE ON SCHEMA caller_controlled TO service_role;")
    try:
        response=json.loads(db.sql(f"SET ROLE service_role; SET search_path=caller_controlled,public; SELECT blipz_v2.start_game_attempt({literal(user)},{literal(games[0]['id'])},{literal(str(uuid4()))});"))
        StartAttemptResponse.model_validate(response)
        assert response['attempt']['started_at'].startswith(db.value("SELECT (clock_timestamp() AT TIME ZONE 'UTC')::date"))
    finally:db.sql('DROP SCHEMA caller_controlled CASCADE;')


def test_sql_missing_answer_kind_cannot_bypass_checks(game):
    db,user,games=game;start(db,user,games[0])
    error=db.sql("UPDATE blipz_v2.daily_game_attempts SET state='completed_incorrect',completed_at=started_at,elapsed_ms=0,answer_mode='typed',final_answer='{}',correct_count=0,question_count=1,is_correct=false,accuracy_bps=0,awarded_points_units=0,score_percentage_bps=0,completion_idempotency_key=gen_random_uuid();",ok=False)
    assert 'check constraint' in error


def test_future_challenge_not_open(rpc_db):
    db=rpc_db;user,games=db.seed(day_offset=1)
    assert 'challenge_not_open' in call(db,'start_game_attempt',user,games[0]['id'],ok=False)


def test_typed_rpc_requires_backend_normalization(game):
    db,user,games=game;start(db,user,games[0])
    error=db.sql(f"SET ROLE service_role; SELECT blipz_v2.submit_game_answer({literal(user)},{literal(games[0]['id'])},{literal(str(uuid4()))},'{{\"kind\":\"typed\",\"text\":\"divisible by 3\"}}');",ok=False)
    assert 'trusted_normalization_required' in error
    assert db.value('SELECT completed_game_count FROM blipz_v2.daily_results')==0
