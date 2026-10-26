"""Offline documentation-fixture checks; never imports the app or contacts services.

The small schema walker covers only keywords emitted in contract.schema.json.
The reference transitions below illustrate the proposed protocol, not a real RPC.
"""
import copy
import hashlib
import json
import re
import unittest
import unicodedata
from datetime import date, datetime
from pathlib import Path
from uuid import UUID

ROOT = Path(__file__).resolve().parent
SCHEMA = json.loads((ROOT / 'contract.schema.json').read_text())
CONTENT = json.loads((ROOT / 'fixtures/content.json').read_text())
PLAY = json.loads((ROOT / 'fixtures/gameplay.json').read_text())


def validate(value, schema):
    if '$ref' in schema:
        return validate(value, SCHEMA['$defs'][schema['$ref'].rsplit('/', 1)[1]])
    if 'anyOf' in schema:
        for candidate in schema['anyOf']:
            try:
                validate(value, candidate)
                return
            except (AssertionError, ValueError, TypeError):
                pass
        raise AssertionError('No anyOf alternative matched')
    if 'const' in schema:
        assert type(value) is type(schema['const']) and value == schema['const']
    if 'enum' in schema:
        assert value in schema['enum']
    kind = schema.get('type')
    if kind == 'object':
        assert isinstance(value, dict)
        assert set(schema.get('required', [])) <= set(value)
        if schema.get('additionalProperties') is False:
            assert set(value) <= set(schema['properties'])
        for key, v in value.items():
            validate(v, schema['properties'][key])
    elif kind == 'array':
        assert isinstance(value, list)
        assert len(value) >= schema.get('minItems', 0)
        assert len(value) <= schema.get('maxItems', float('inf'))
        for item in value:
            validate(item, schema['items'])
    elif kind == 'string':
        assert isinstance(value, str)
        assert len(value) >= schema.get('minLength', 0)
        assert len(value) <= schema.get('maxLength', float('inf'))
        if 'pattern' in schema:
            assert re.search(schema['pattern'], value)
        if schema.get('format') == 'uuid':
            assert str(UUID(value)) == value
        elif schema.get('format') == 'date':
            assert date.fromisoformat(value).isoformat() == value
        elif schema.get('format') == 'date-time':
            assert value.endswith('Z')
            assert datetime.fromisoformat(value.replace('Z', '+00:00')).tzinfo
    elif kind == 'integer':
        assert type(value) is int
        assert schema.get('minimum', -float('inf')) <= value <= schema.get('maximum', float('inf'))
    elif kind == 'boolean':
        assert type(value) is bool
    elif kind == 'null':
        assert value is None


def model(value, name):
    validate(value, SCHEMA['$defs'][name])


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode()


def normalize(value):
    return ' '.join(unicodedata.normalize('NFKC', value).casefold().split()).rstrip('.!?')


def rule(value, spec):
    assert spec['family'] == 'integer_divisible_by'
    return value % spec['divisor'] == 0


def validate_hidden(public, private):
    yes, no = public['belongs'], public['does_not_belong']
    assert len(set(yes + no)) == len(yes + no)
    domain = range(public['domain']['minimum'], public['domain']['maximum'] + 1)
    assert all(v in domain for v in yes + no)
    assert all(rule(v, private['rule_spec']) for v in yes)
    assert all(not rule(v, private['rule_spec']) for v in no)
    candidates = private['choices']
    signatures = [tuple(rule(n, c['rule_spec']) for n in domain) for c in candidates]
    assert len(set(signatures)) == 4
    fits = [c['id'] for c in candidates if all(rule(v, c['rule_spec']) for v in yes) and all(not rule(v, c['rule_spec']) for v in no)]
    assert fits == [private['correct_choice_id']]


def validate_attempt(a):
    model(a, 'DailyGameAttempt')
    assert a['state'] in ('completed_correct', 'completed_incorrect')
    start = datetime.fromisoformat(a['started_at'].replace('Z', '+00:00'))
    end = datetime.fromisoformat(a['completed_at'].replace('Z', '+00:00'))
    assert int((end - start).total_seconds() * 1000) == a['elapsed_ms']
    assert a['started_at'][:10] == a['challenge_date'] == a['completed_at'][:10]
    assert a['choices_revealed'] == (a['choices_revealed_at'] is not None)
    assert a['available_points_units'] == (1875 if a['choices_revealed'] else 2500)
    if a['choices_revealed']:
        assert start <= datetime.fromisoformat(a['choices_revealed_at'].replace('Z', '+00:00')) <= end
    if a['game_type'] == 'trivia':
        assert a['answer_mode'] == 'native_mcq' and not a['choices_revealed']
        assert a['question_count'] == 5
        solutions = {q['question_id']: q['correct_choice_id'] for q in CONTENT['revisions'][2]['private_solution']['questions']}
        answers = a['final_answer']['answers']
        assert len(answers) == len({x['question_id'] for x in answers}) == 5
        assert {x['question_id'] for x in answers} == set(solutions)
        correct = sum(x['selected_option_id'] == solutions[x['question_id']] for x in answers)
        assert correct == a['correct_count']
        expected = 500 * correct
    else:
        assert a['question_count'] == 1
        assert a['answer_mode'] == ('assisted_mcq' if a['choices_revealed'] else 'typed')
        revision = next(r for r in CONTENT['revisions'] if r['id'] == a['content_revision_id'])
        solution = revision['private_solution']
        answer = a['final_answer']
        correct = (answer['selected_option_id'] == solution['correct_choice_id']) if a['choices_revealed'] else (normalize(answer['text']) in {normalize(s) for s in solution['answers']['accepted_aliases']})
        assert int(correct) == a['correct_count']
        expected = a['available_points_units'] if correct else 0
    assert a['awarded_points_units'] == expected
    assert a['accuracy_bps'] == a['correct_count'] * 10000 // a['question_count']
    assert a['score_percentage_bps'] == expected * 10000 // a['max_points_units']
    assert a['is_correct'] == (a['correct_count'] == a['question_count'])
    assert (a['state'] == 'completed_correct') == a['is_correct']


def ranking_key(a, performance=False):
    head = (-a['correct_count'], a['elapsed_ms']) if a['game_type'] == 'trivia' else (-a['awarded_points_units'], a['choices_revealed'], a['elapsed_ms'])
    return head if performance else head + (a['completed_at'], a['id'])


def percentile(n, better, tied):
    assert 0 <= better < n and 1 <= tied <= n - better
    if n < 100:
        return None, None
    return (10000 * (n - better - tied) // n, (100 * (better + tied) + n - 1) // n)


def reference_trace(actions):
    state, points, errors = 'not_started', None, []
    for action in actions:
        terminal = state in ('completed_correct', 'completed_incorrect')
        if terminal:
            if action == 'reveal': errors.append('attempt_completed')
            continue
        if state == 'expired':
            errors.append('challenge_closed')
        elif action == 'deadline':
            state = 'expired'
        elif action == 'start':
            if state == 'not_started': state = 'active'
        elif state == 'not_started':
            errors.append('attempt_not_started')
        elif action == 'reveal':
            state = 'choices_revealed'
        elif action.startswith('typed_') and state != 'active':
            errors.append('answer_mode_mismatch')
        elif action.startswith('choice_') and state != 'choices_revealed':
            errors.append('answer_mode_mismatch')
        else:
            correct = action.endswith('_correct')
            points = (1875 if state == 'choices_revealed' else 2500) if correct else 0
            state = 'completed_correct' if correct else 'completed_incorrect'
    return state, points, errors


class FixtureChecks(unittest.TestCase):
    def test_domain_shapes(self):
        model(CONTENT['challenge'], 'DailyChallenge')
        for r in CONTENT['revisions']:
            model(r, 'GameContentRevision')
            name = {'hidden_rule':'HiddenRule','riddle':'Riddle','trivia':'Trivia','world_guess':'WorldGuess'}[r['game_type']]
            model(r['public_payload'], name + 'PublicPayload')
            model(r['private_solution'], name + 'PrivateSolution')
        for public in CONTENT['public_content_views']: model(public, 'PublicGameContentRevision')
        for i in CONTENT['challenge']['games']: model(i, 'DailyGameInstance')
        for a in PLAY['attempts'] + PLAY['completed_day_attempts']: model(a, 'DailyGameAttempt')
        for key, name in [('daily_result','DailyResult'),('daily_activity','DailyActivity'),('completed_review','CompletedGameReview'),('game_rank_summary','GameRankStatSummary')]: model(PLAY[key], name)
        for row in PLAY['overall_leaderboard']: model(row, 'OverallLeaderboardRow')
        for row in PLAY['hidden_rule_leaderboard'] + PLAY['trivia_leaderboard']: model(row, 'GameLeaderboardRow')
        for row in PLAY['history']: model(row, 'HistoryRow')

    def test_api_examples_match_domain_contracts(self):
        examples=json.loads((ROOT/'fixtures/api-examples.json').read_text())
        for example in examples['examples']:
            name=example['name']; response=example['response']
            if name=='today': model(response['challenge'],'DailyChallenge')
            elif name in ('progress','start','reveal','submit','review','rank_summary','completed_reveal_rejected'):
                model(response,{'progress':'DailyProgress','start':'StartAttemptResponse','reveal':'RevealChoicesResponse','submit':'SubmitResponse','review':'CompletedGameReview','rank_summary':'GameRankStatSummary','completed_reveal_rejected':'Error'}[name])
            elif name=='result': model(response['result'],'DailyResult')
            elif name in ('overall_leaderboard','game_leaderboard','history'):
                for row in response['rows']: model(row,{'overall_leaderboard':'OverallLeaderboardRow','game_leaderboard':'GameLeaderboardRow','history':'HistoryRow'}[name])
            if name=='reveal':
                for option in response['choices']: self.assertEqual(set(option),{'id','text'})
            if name=='submit': model(example['request']['answer'],'FinalAnswer')
        self.assertEqual(len(examples['examples']),12)

    def test_lineup_identity(self):
        c = CONTENT['challenge']; games = c['games']
        self.assertEqual([g['game_type'] for g in games], ['hidden_rule','riddle','trivia','world_guess'])
        self.assertEqual([g['position'] for g in games], [1,2,3,4])
        self.assertEqual(sum(g['max_points_units'] for g in games), 10000)
        for g in games:
            revision = next(r for r in CONTENT['revisions'] if r['id'] == g['content_revision_id'])
            self.assertEqual(g['challenge_id'], c['id'])
            self.assertEqual(g['game_type'], revision['game_type'])
            self.assertEqual(g['variant'], revision['variant'])

    def test_public_projection_does_not_leak(self):
        for r, public in zip(CONTENT['revisions'], CONTENT['public_content_views']):
            self.assertEqual(set(public), {'id','game_type','variant','schema_version','public_payload'})
            self.assertEqual(public['public_payload'], r['public_payload'])
            body = json.dumps(public)
            for forbidden in ['private_solution','correct_choice_id','accepted_aliases','canonical_explanation','rule_spec']:
                self.assertNotIn(forbidden, body)
            if r['game_type'] != 'trivia': self.assertNotIn('choices', public['public_payload'])

    def test_public_leak_mutation_rejected(self):
        public = copy.deepcopy(CONTENT['public_content_views'][0])
        public['public_payload']['correct_choice_id'] = 'B'
        with self.assertRaises(AssertionError): model(public, 'PublicGameContentRevision')

    def test_revision_hashes(self):
        for r in CONTENT['revisions']:
            digest = hashlib.sha256(canonical({k:r[k] for k in ['public_payload','private_solution']})).hexdigest()
            self.assertEqual(digest, r['validation']['content_sha256'])

    def test_hidden_rule_oracle(self):
        r = CONTENT['revisions'][0]
        validate_hidden(r['public_payload'], r['private_solution'])
        self.assertEqual([n % 3 for n in r['public_payload']['belongs']], [0,0,0,0])
        self.assertEqual([n % 3 for n in r['public_payload']['does_not_belong']], [1,2,1,1])

    def test_hidden_rule_bad_negative_rejected(self):
        r = copy.deepcopy(CONTENT['revisions'][0]); r['public_payload']['does_not_belong'][0] = 9
        with self.assertRaises(AssertionError): validate_hidden(r['public_payload'],r['private_solution'])

    def test_hidden_rule_equivalent_choice_rejected(self):
        r = copy.deepcopy(CONTENT['revisions'][0]); r['private_solution']['choices'][0]['rule_spec']['divisor'] = 3
        with self.assertRaises(AssertionError): validate_hidden(r['public_payload'],r['private_solution'])

    def test_alias_normalization(self):
        self.assertEqual(normalize('  DIVISIBLE   BY ３! '), 'divisible by 3')
        self.assertNotEqual(normalize('not divisible by 3'), 'divisible by 3')
        for r in CONTENT['revisions']:
            if r['game_type'] == 'trivia': continue
            s = r['private_solution']
            self.assertIn(normalize(s['canonical_answer']), {normalize(x) for x in s['answers']['accepted_aliases']})

    def test_choice_and_question_identity(self):
        for r in CONTENT['revisions']:
            if r['game_type'] == 'trivia':
                public=r['public_payload']['questions']; private=r['private_solution']['questions']
                self.assertEqual(len(public),5)
                self.assertEqual({q['id'] for q in public}, {q['question_id'] for q in private})
                sets=[q['choices'] for q in public]
            else: sets=[r['private_solution']['choices']]
            for choices in sets:
                self.assertEqual({c['id'] for c in choices}, set('ABCD'))
                self.assertEqual(len({normalize(c['text']) for c in choices}),4)

    def test_attempt_grading_and_timing(self):
        for a in PLAY['attempts'] + PLAY['completed_day_attempts']: validate_attempt(a)

    def test_score_mutations_rejected(self):
        for source in [PLAY['attempts'][0], PLAY['attempts'][2], PLAY['attempts'][3]]:
            a=copy.deepcopy(source); a['awarded_points_units']+=1
            with self.assertRaises(AssertionError): validate_attempt(a)

    def test_total_and_completion(self):
        r=PLAY['daily_result']; attempts=PLAY['completed_day_attempts']
        self.assertEqual(r['total_points_units'],8875)
        self.assertEqual(r['total_points_units'],sum(a['awarded_points_units'] for a in attempts))
        self.assertEqual(r['total_elapsed_ms'],150000)
        self.assertEqual(r['completed_at'],max(a['completed_at'] for a in attempts))
        self.assertTrue(r['overall_eligible'])
        self.assertEqual(len({a['game_instance_id'] for a in attempts}),4)
        self.assertEqual({a['user_id'] for a in attempts},{r['user_id']})

    def test_review_and_activity_identity(self):
        a=PLAY['completed_day_attempts'][0]; review=PLAY['completed_review']; activity=PLAY['daily_activity']
        for key in ['challenge_id','game_instance_id','content_revision_id']: self.assertEqual(review[key],a[key])
        self.assertEqual(review['attempt_id'],a['id'])
        self.assertEqual(activity['source_attempt_id'],a['id'])
        self.assertEqual(activity['first_completed_at'],a['completed_at'])
        self.assertEqual(activity['date'],a['challenge_date'])

    def test_typed_accuracy_dominates_speed(self):
        ordered=sorted(PLAY['attempts'][:4],key=ranking_key)
        self.assertEqual([a['awarded_points_units'] for a in ordered],[2500,2500,1875,0])
        self.assertEqual([a['elapsed_ms'] for a in ordered[:2]],[40000,60000])
        self.assertEqual([r['elapsed_ms'] for r in PLAY['hidden_rule_leaderboard']],[40000,60000,8000,5000])

    def test_trivia_accuracy_dominates_speed(self):
        ordered=sorted(PLAY['attempts'][4:],key=ranking_key)
        self.assertEqual([(a['correct_count'],a['elapsed_ms']) for a in ordered],[(5,60000),(5,90000),(4,8000)])
        self.assertEqual([r['correct_count'] for r in PLAY['trivia_leaderboard']],[5,5,4])

    def test_performance_ties_ignore_timestamp_and_uuid(self):
        a=copy.deepcopy(PLAY['attempts'][0]); b=copy.deepcopy(a)
        b['completed_at']='2099-12-29T15:00:40.000Z'; b['id']='ffffffff-ffff-4fff-8fff-ffffffffffff'
        self.assertEqual(ranking_key(a,True),ranking_key(b,True))
        self.assertNotEqual(ranking_key(a),ranking_key(b))

    def test_full_cohort_percentile_arithmetic(self):
        for row in PLAY['percentile_math_examples']:
            self.assertEqual(percentile(row['population'],row['strictly_better'],row['tied']),(row['percentile_bps'],row['top_percent_display']))
        self.assertIsNone(PLAY['game_rank_summary']['percentile_bps'])
        self.assertEqual(percentile(100,0,1),(9900,1))

    def test_reference_transition_scenarios(self):
        trace=json.loads((ROOT/'fixtures/state-transitions.json').read_text())
        for s in trace['scenarios']:
            self.assertEqual(reference_trace(s['actions']),(s['state'],s['points'],s['errors']),s['name'])

    def test_assets_match_hash_and_neutral_public_metadata(self):
        for r in CONTENT['revisions']:
            if r['game_type']!='world_guess': continue
            a=r['public_payload']['asset']; local=ROOT/'fixtures/assets'/a['uri'].rsplit('/',1)[1]
            self.assertEqual(hashlib.sha256(local.read_bytes()).hexdigest(),a['sha256'])
            secret=r['private_solution']['canonical_answer'].casefold()
            self.assertNotIn('source',a)
            self.assertNotIn(secret,a['uri'].casefold())
            self.assertNotIn(secret,a['alt_text'].casefold())
            self.assertNotIn(secret,local.read_text().casefold())

    def test_local_only_and_legacy_history(self):
        self.assertTrue(CONTENT['fixture_only']); self.assertFalse(CONTENT['publication_eligible'])
        for r in CONTENT['revisions']:
            self.assertEqual(r['validation']['human_approval'],'not_reviewed')
            self.assertNotEqual(r['status'],'approved')
        legacy=PLAY['history'][1]
        self.assertEqual((legacy['total_points_units'],legacy['max_points_units']),(2950,3500))
        self.assertIsNone(legacy['completed']); self.assertEqual(legacy['games'],[])


if __name__ == '__main__':
    unittest.main(verbosity=2)
