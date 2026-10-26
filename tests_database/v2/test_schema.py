import pytest
from .local_db import literal
from uuid import uuid4


def test_additive_migration_preserves_v1(schema_db):
    assert schema_db.v1_snapshot()==schema_db.baseline
    assert schema_db.value("SELECT count(*) FROM information_schema.tables WHERE table_schema IN ('blipz_v2','blipz_v2_private')")==8


def test_full_approved_lineup_and_no_v1_data_changes(schema_db):
    _,games=schema_db.seed()
    assert len(games)==4
    assert schema_db.value('SELECT count(*) FROM blipz_v2.daily_game_instances')==4
    assert schema_db.v1_snapshot()==schema_db.baseline


@pytest.mark.parametrize('role',['anon','authenticated'])
@pytest.mark.parametrize('table',['blipz_v2.game_content_revisions','blipz_v2_private.game_solutions','blipz_v2.daily_game_attempts','blipz_v2.daily_results','blipz_v2.daily_activity'])
def test_clients_cannot_read_or_mutate(schema_db,role,table):
    assert 'permission denied' in schema_db.sql(f'SET ROLE {role}; SELECT * FROM {table};',ok=False)
    assert 'permission denied' in schema_db.sql(f'SET ROLE {role}; DELETE FROM {table};',ok=False)


def test_service_cannot_directly_mutate_gameplay_or_read_private(schema_db):
    for table in ['daily_game_attempts','daily_results','daily_activity']:
        assert 'permission denied' in schema_db.sql(f'SET ROLE service_role; DELETE FROM blipz_v2.{table};',ok=False)
    assert 'permission denied' in schema_db.sql('SET ROLE service_role; SELECT * FROM blipz_v2_private.game_solutions;',ok=False)


def test_revision_and_solution_immutable(schema_db):
    schema_db.seed()
    for statement in ["UPDATE blipz_v2.game_content_revisions SET public_payload='{}'", "DELETE FROM blipz_v2_private.game_solutions"]:
        assert 'Immutable' in schema_db.sql(statement,ok=False)


def test_published_instances_and_challenge_immutable(schema_db):
    schema_db.seed()
    for statement in ["UPDATE blipz_v2.daily_challenges SET date=date+1", "UPDATE blipz_v2.daily_game_instances SET max_points_units=1", "DELETE FROM blipz_v2.daily_game_instances"]:
        assert 'immutable' in schema_db.sql(statement,ok=False)


def test_invalid_foreign_key(schema_db):
    assert 'foreign key' in schema_db.sql("INSERT INTO blipz_v2_private.game_solutions VALUES ('99999999-9999-4999-8999-999999999999','{}','{}','answer-normalization-v1');",ok=False)


def test_duplicate_date(schema_db):
    schema_db.seed()
    assert 'duplicate key' in schema_db.sql("INSERT INTO blipz_v2.daily_challenges(date,lineup_revision,status,opens_at,closes_at) SELECT date,gen_random_uuid(),'draft',opens_at,closes_at FROM blipz_v2.daily_challenges;",ok=False)


def test_invalid_day_boundary(schema_db):
    assert 'check constraint' in schema_db.sql("INSERT INTO blipz_v2.daily_challenges(date,lineup_revision,status,opens_at,closes_at) VALUES ('2099-01-01',gen_random_uuid(),'draft','2099-01-01T01:00:00Z','2099-01-02T01:00:00Z');",ok=False)


def test_incomplete_publication_rolls_back(schema_db):
    count=schema_db.value('SELECT count(*) FROM blipz_v2.daily_challenges')
    assert 'lineup' in schema_db.sql("BEGIN; INSERT INTO blipz_v2.daily_challenges(date,lineup_revision,status,opens_at,closes_at,published_at) VALUES ('2099-01-01',gen_random_uuid(),'published','2099-01-01T00:00:00Z','2099-01-02T00:00:00Z',clock_timestamp()); COMMIT;",ok=False)
    assert schema_db.value('SELECT count(*) FROM blipz_v2.daily_challenges')==count


def test_result_not_allowed_to_invent_score(schema_db):
    user,games=schema_db.seed()
    assert 'terminal contributions' in schema_db.sql(f"INSERT INTO blipz_v2.daily_results(user_id,challenge_id,date,total_points_units) SELECT {literal(user)},id,date,2500 FROM blipz_v2.daily_challenges;",ok=False)


def test_every_v2_table_has_rls(schema_db):
    assert schema_db.value("SELECT bool_and(c.relrowsecurity) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname IN ('blipz_v2','blipz_v2_private') AND c.relkind='r'") is True


def test_indexes_present(schema_db):
    indexes=schema_db.sql("SELECT indexname FROM pg_indexes WHERE schemaname='blipz_v2';")
    for name in ['v2_game_ranking','v2_trivia_ranking','v2_overall_ranking','v2_history','v2_user_progress']:assert name in indexes


def active(schema_db):
    user,games=schema_db.seed();instance=games[0];attempt=str(uuid4())
    schema_db.sql(f"BEGIN; INSERT INTO blipz_v2.daily_results(user_id,challenge_id,date) SELECT {literal(user)},id,date FROM blipz_v2.daily_challenges; INSERT INTO blipz_v2.daily_game_attempts(id,user_id,challenge_id,game_instance_id,content_revision_id,game_type,challenge_date,started_at,max_points_units) SELECT {literal(attempt)},{literal(user)},i.challenge_id,i.id,i.content_revision_id,i.game_type,c.date,clock_timestamp(),i.max_points_units FROM blipz_v2.daily_game_instances i JOIN blipz_v2.daily_challenges c ON c.id=i.challenge_id WHERE i.id={literal(instance['id'])}; COMMIT;")
    return user,attempt


def test_unique_attempt_per_user_instance(schema_db):
    _,a=active(schema_db)
    assert 'duplicate key' in schema_db.sql(f"INSERT INTO blipz_v2.daily_game_attempts(user_id,challenge_id,game_instance_id,content_revision_id,game_type,challenge_date,started_at,max_points_units) SELECT user_id,challenge_id,game_instance_id,content_revision_id,game_type,challenge_date,started_at,max_points_units FROM blipz_v2.daily_game_attempts WHERE id={literal(a)};",ok=False)


def test_timer_identity_cannot_change(schema_db):
    _,a=active(schema_db)
    assert 'timer cannot change' in schema_db.sql(f"UPDATE blipz_v2.daily_game_attempts SET started_at=started_at+interval '1 second' WHERE id={literal(a)};",ok=False)


def test_reveal_cannot_be_reversed(schema_db):
    _,a=active(schema_db)
    schema_db.sql(f"UPDATE blipz_v2.daily_game_attempts SET state='choices_revealed',choices_revealed=true,choices_revealed_at=clock_timestamp(),available_points_units=1875 WHERE id={literal(a)};")
    assert 'irreversible' in schema_db.sql(f"UPDATE blipz_v2.daily_game_attempts SET state='active',choices_revealed=false,choices_revealed_at=NULL,available_points_units=2500 WHERE id={literal(a)};",ok=False)


def test_zero_completion_terminal_immutable_and_derived(schema_db):
    user,a=active(schema_db)
    schema_db.sql(f"BEGIN; UPDATE blipz_v2.daily_game_attempts SET state='completed_incorrect',completed_at=started_at,elapsed_ms=0,answer_mode='typed',final_answer='{{\"kind\":\"typed\",\"text\":\"wrong\"}}',correct_count=0,question_count=1,is_correct=false,accuracy_bps=0,awarded_points_units=0,score_percentage_bps=0,completion_idempotency_key=gen_random_uuid() WHERE id={literal(a)}; UPDATE blipz_v2.daily_results SET completed_game_count=1 WHERE user_id={literal(user)}; COMMIT;")
    assert schema_db.value('SELECT completed_game_count FROM blipz_v2.daily_results')==1
    assert 'immutable' in schema_db.sql(f"UPDATE blipz_v2.daily_game_attempts SET state='active' WHERE id={literal(a)};",ok=False)
    assert 'cannot be deleted' in schema_db.sql(f"DELETE FROM blipz_v2.daily_game_attempts WHERE id={literal(a)};",ok=False)


def test_schema_transaction_can_rollback_without_touching_v1():
    from .local_db import LocalDB, ROOT
    db=LocalDB().start()
    try:
        text=(ROOT/'sql/20261026_v2_schema.sql').read_text()
        text=text.replace('BEGIN;','',1).rsplit('COMMIT;',1)[0]
        db.sql('BEGIN;'+text+'ROLLBACK;')
        assert db.value("SELECT count(*) FROM pg_namespace WHERE nspname='blipz_v2'")==0
        assert db.v1_snapshot()==db.baseline
    finally:db.stop()
