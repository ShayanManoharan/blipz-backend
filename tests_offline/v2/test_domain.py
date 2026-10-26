"""Offline contract, adversarial-content, scoring, telemetry and ranking coverage."""
import ast
from copy import deepcopy
import hashlib
import inspect
import json
from pathlib import Path
import subprocess
import sys

import pytest
from pydantic import ValidationError

from app.v2 import models as m
from app.v2.answers import matches_alias, normalize_answer
from app.v2.ranking import full_population_percentile, game_performance_key, game_ranking_key, overall_ranking_key
from app.v2.rules import evaluate_integer_rule, validate_hidden_rule
from app.v2.scoring import percentage_bps, trivia_points, typed_game_points
from app.v2.validation import public_revision_view, validate_publishable_revision

ROOT = Path(__file__).parents[2]
FIXTURES = ROOT / "docs/v2-contracts/fixtures"
CONTENT = json.loads((FIXTURES / "content.json").read_text())
PLAY = json.loads((FIXTURES / "gameplay.json").read_text())
API = json.loads((FIXTURES / "api-examples.json").read_text())
SCHEMA = json.loads((ROOT / "docs/v2-contracts/contract.schema.json").read_text())


def rehash(data):
    content = {k: data[k] for k in ("public_payload", "private_solution")}
    data["validation"]["content_sha256"] = hashlib.sha256(json.dumps(content, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()
    return data


def revision(index):
    return m.GameContentRevision.model_validate(deepcopy(CONTENT["revisions"][index]))


def active_attempt():
    a = deepcopy(PLAY["attempts"][0])
    a["state"] = "active"
    for key in ("completed_at", "elapsed_ms", "answer_mode", "final_answer", "correct_count", "question_count", "is_correct", "accuracy_bps", "awarded_points_units", "score_percentage_bps", "completion_idempotency_key"):
        a[key] = None
    return a


@pytest.mark.parametrize("correct,assisted,expected", [(True,False,2500),(True,True,1875),(False,False,0),(False,True,0)])
def test_typed_scores(correct, assisted, expected):
    assert typed_game_points(correct=correct, choices_revealed=assisted) == expected


@pytest.mark.parametrize("correct", range(6))
def test_trivia_scores(correct):
    assert trivia_points(correct) == correct * 500


@pytest.mark.parametrize("bad", [-1,6,True,1.0,"1",None])
def test_trivia_rejects_non_integer_or_out_of_bounds(bad):
    with pytest.raises(ValueError): trivia_points(bad)


@pytest.mark.parametrize("correct,assisted", [(1,False),(True,0),(None,True),("yes",False)])
def test_scoring_rejects_coerced_boolean(correct, assisted):
    with pytest.raises(ValueError): typed_game_points(correct=correct,choices_revealed=assisted)


def test_speed_not_part_of_scoring_api():
    assert set(inspect.signature(typed_game_points).parameters) == {"correct","choices_revealed"}
    assert set(inspect.signature(trivia_points).parameters) == {"correct_count"}


@pytest.mark.parametrize("awarded,max_units,expected", [(2500,2500,10000),(1875,2500,7500),(0,2500,0),(2000,2500,8000)])
def test_integer_percentage(awarded,max_units,expected):
    assert percentage_bps(awarded,max_units) == expected


@pytest.mark.parametrize("awarded,max_units", [(-1,2500),(2501,2500),(0,0),(True,2500),(1,False),(1.0,2500)])
def test_percentage_rejects_invalid_units(awarded,max_units):
    with pytest.raises(ValueError): percentage_bps(awarded,max_units)


@pytest.mark.parametrize("raw,expected", [("  DIVISIBLE   BY ３! ","divisible by 3"),("Straße?","strasse"),("Ａ　ＰＩＡＮＯ...","a piano"),("a\n\tpiano","a piano"),("piano!?!","piano"),("not divisible by 3","not divisible by 3"),("",""),("...",""),("a-piano","a-piano"),("piano,","piano,")])
def test_normalization_exact_v1(raw,expected):
    assert normalize_answer(raw) == expected


@pytest.mark.parametrize("value,expected", [("Piano!",True),("pianno",False),("keyboard",False),("not piano",False),("",False),("...",False)])
def test_only_approved_aliases(value,expected):
    assert matches_alias(value,["piano","a piano"]) is expected


@pytest.mark.parametrize("bad", [None,1,True,[],{}])
def test_normalization_requires_text(bad):
    with pytest.raises(ValueError): normalize_answer(bad)


@pytest.mark.parametrize("index", range(5))
def test_content_fixture_round_trip(index):
    data = CONTENT["revisions"][index]
    assert revision(index).model_dump(mode="json") == data


@pytest.mark.parametrize("name", [n for n,s in SCHEMA["$defs"].items() if s.get("type")=="object"])
def test_every_documented_object_has_exact_fields(name):
    model = getattr(m,name)
    assert set(model.model_fields) == set(SCHEMA["$defs"][name]["properties"])
    assert all(f.is_required() for f in model.model_fields.values())


def test_exact_wire_examples_round_trip():
    models={"today":m.ChallengeResponse,"progress":m.DailyProgress,"start":m.StartAttemptResponse,"reveal":m.RevealChoicesResponse,"submit":m.SubmitResponse,"review":m.CompletedGameReview,"result":m.ResultResponse,"overall_leaderboard":m.OverallLeaderboardResponse,"game_leaderboard":m.GameLeaderboardResponse,"rank_summary":m.GameRankStatSummary,"history":m.HistoryResponse,"completed_reveal_rejected":m.Error}
    for example in API["examples"]:
        result=models[example["name"]].model_validate(example["response"])
        assert result.model_dump(mode="json") == example["response"]
        if example["method"]=="POST":
            request_model=m.SubmitRequest if example["name"]=="submit" else m.MutationRequest
            assert request_model.model_validate(example["request"]).model_dump(mode="json")==example["request"]


@pytest.mark.parametrize("name", ["hidden_rule","riddle","trivia","world_guess"])
def test_extra_private_fields_rejected_in_public_payload(name):
    raw=deepcopy(next(r for r in CONTENT["public_content_views"] if r["game_type"]==name))
    raw["public_payload"]["correct_choice_id"]="A"
    with pytest.raises(ValidationError): m.PublicGameContentRevision.model_validate(raw)


def test_public_projection_never_includes_private_solution():
    for index in range(5):
        projected=public_revision_view(revision(index)).model_dump(mode="json")
        assert projected == CONTENT["public_content_views"][index]
        assert "private_solution" not in projected and "validation" not in projected


@pytest.mark.parametrize("field,value", [("schema_version",True),("schema_version","1"),("status","published"),("created_at","2099-12-29T00:00:00+00:00"),("id","invalid-id")])
def test_revision_invalid_types_and_literals(field,value):
    raw=deepcopy(CONTENT["revisions"][0]); raw[field]=value
    with pytest.raises(ValidationError): m.GameContentRevision.model_validate(raw)


def test_unknown_field_rejected():
    with pytest.raises(ValidationError): m.MutationRequest(idempotency_key=PLAY["attempts"][0]["id"],user_id=PLAY["attempts"][0]["user_id"])


def test_hidden_rule_oracle_exhaustive_domain():
    r=revision(0)
    validate_hidden_rule(r.public_payload,r.private_solution)
    for n in range(1,100): assert evaluate_integer_rule(n,r.private_solution.rule_spec) == (n in range(3,100,3))


def test_direct_hidden_rule_validator_checks_choice_identity():
    public=m.HiddenRulePublicPayload.model_validate(CONTENT["revisions"][0]["public_payload"])
    raw=deepcopy(CONTENT["revisions"][0]["private_solution"])
    raw["choices"][0]["id"]="B"
    private=m.HiddenRulePrivateSolution.model_validate(raw)
    with pytest.raises(ValueError,match="distinct A-D"):
        validate_hidden_rule(public,private)


@pytest.mark.parametrize("case", ["negative_satisfies","positive_fails","duplicate_positive","contradictory","outside_domain","reversed_domain","huge_domain","equivalent_choices","wrong_correct_id","duplicate_choice_ids","duplicate_labels","mismatched_kind","bad_hash","blank_alias","duplicate_alias","patterns"])
def test_hidden_rule_bad_content_rejected(case):
    raw=deepcopy(CONTENT["revisions"][0]); public=raw["public_payload"]; private=raw["private_solution"]
    if case=="negative_satisfies": public["does_not_belong"][0]=9
    elif case=="positive_fails": public["belongs"][0]=8
    elif case=="duplicate_positive": public["belongs"][1]=6
    elif case=="contradictory": public["does_not_belong"][0]=6
    elif case=="outside_domain": public["belongs"][0]=102
    elif case=="reversed_domain": public["domain"]={"minimum":99,"maximum":1,"type":"integer"}
    elif case=="huge_domain": public["domain"]["maximum"]=10001
    elif case=="equivalent_choices": private["choices"][0]["rule_spec"]["divisor"]=3
    elif case=="wrong_correct_id": private["correct_choice_id"]="A"
    elif case=="duplicate_choice_ids": private["choices"][0]["id"]="B"
    elif case=="duplicate_labels": private["choices"][0]["text"]="MULTIPLES OF THREE"
    elif case=="mismatched_kind": raw["game_type"]="riddle"
    elif case=="blank_alias": private["answers"]["accepted_aliases"][0]="..."
    elif case=="duplicate_alias": private["answers"]["accepted_aliases"].append("DIVISIBLE BY 3!")
    elif case=="patterns": private["answers"]["approved_patterns"]=[".*"]
    rehash(raw)
    if case=="bad_hash": raw["validation"]["content_sha256"]="0"*64
    with pytest.raises(ValidationError): m.GameContentRevision.model_validate(raw)


@pytest.mark.parametrize("divisor", [0,1,100,True,3.0,"3"])
def test_divisor_invalid(divisor):
    with pytest.raises(ValidationError): m.RuleSpec(family="integer_divisible_by",divisor=divisor)


@pytest.mark.parametrize("family", ["python_eval","regex","is_prime","unknown"])
def test_unimplemented_rule_family_rejected(family):
    with pytest.raises(ValidationError): m.RuleSpec(family=family,divisor=3)


@pytest.mark.parametrize("case", ["blank_riddle","missing_choice","duplicate_choice","wrong_correct","alias_absent","pattern"])
def test_riddle_structural_rejections(case):
    raw=deepcopy(CONTENT["revisions"][1]); p=raw["private_solution"]
    if case=="blank_riddle": raw["public_payload"]["riddle"]="  "
    elif case=="missing_choice": p["choices"].pop()
    elif case=="duplicate_choice": p["choices"][0]["text"]="Piano"
    elif case=="wrong_correct": p["correct_choice_id"]="C"
    elif case=="alias_absent": p["answers"]["accepted_aliases"]=["keyboard"]
    elif case=="pattern": p["answers"]["approved_patterns"]=["p.*"]
    with pytest.raises(ValidationError): m.GameContentRevision.model_validate(rehash(raw))


@pytest.mark.parametrize("case", ["four_questions","six_questions","duplicate_ids","duplicate_text","foreign_solution","missing_solution","three_choices","duplicate_option","bad_correct"])
def test_trivia_structural_rejections(case):
    raw=deepcopy(CONTENT["revisions"][2]); p=raw["public_payload"];s=raw["private_solution"]
    if case=="four_questions": p["questions"].pop()
    elif case=="six_questions": p["questions"].append(deepcopy(p["questions"][0]))
    elif case=="duplicate_ids": p["questions"][1]["id"]="q0"
    elif case=="duplicate_text": p["questions"][1]["question"]=p["questions"][0]["question"]
    elif case=="foreign_solution": s["questions"][0]["question_id"]="foreign"
    elif case=="missing_solution": s["questions"].pop()
    elif case=="three_choices": p["questions"][0]["choices"].pop()
    elif case=="duplicate_option": p["questions"][0]["choices"][0]["id"]="B"
    elif case=="bad_correct": s["questions"][0]["correct_choice_id"]="E"
    with pytest.raises(ValidationError): m.GameContentRevision.model_validate(rehash(raw))


@pytest.mark.parametrize("case", ["landmark","wrong_variant","bad_url","http","embedded_credentials","spoiler_alt","spoiler_filename","bad_hash","wrong_choice","no_entity"])
def test_world_structural_rejections(case):
    raw=deepcopy(CONTENT["revisions"][3]);p=raw["public_payload"];s=raw["private_solution"]
    if case=="landmark": raw["variant"]=p["variant"]="landmark"
    elif case=="wrong_variant": raw["variant"]="outline"
    elif case=="bad_url": p["asset"]["uri"]="file:///secret.svg"
    elif case=="http": p["asset"]["uri"]="http://assets.example.invalid/opaque.svg"
    elif case=="embedded_credentials": p["asset"]["uri"]="https://username:password@assets.example.invalid/opaque.svg"
    elif case=="spoiler_alt": p["asset"]["alt_text"]="Japan flag"
    elif case=="spoiler_filename": p["asset"]["uri"]="https://assets.example.invalid/Japan.svg"
    elif case=="bad_hash": p["asset"]["sha256"]="zz"
    elif case=="wrong_choice": s["correct_choice_id"]="A"
    elif case=="no_entity": s["entity_id"]="  "
    with pytest.raises(ValidationError): m.GameContentRevision.model_validate(rehash(raw))


@pytest.mark.parametrize("index", range(5))
def test_unreviewed_fixture_not_publishable(index):
    with pytest.raises(ValueError,match="approved"): validate_publishable_revision(revision(index))


def test_approved_fixture_scheme_still_rejected():
    raw=deepcopy(CONTENT["revisions"][3]);raw["status"]="approved"
    raw["validation"].update(human_approval="approved",approved_by="local-reviewer",approved_at="2099-12-29T00:01:00.000Z")
    with pytest.raises(ValidationError,match="HTTPS"): m.GameContentRevision.model_validate(raw)


def test_structural_approval_does_not_make_network_calls():
    raw=deepcopy(CONTENT["revisions"][3]); raw["public_payload"]["asset"]["uri"]="https://assets.example.invalid/opaque.svg"; raw["status"]="approved"
    raw["validation"].update(human_approval="approved",approved_by="synthetic-editor",approved_at="2099-12-29T00:01:00.000Z")
    validate_publishable_revision(m.GameContentRevision.model_validate(rehash(raw)))


@pytest.mark.parametrize("index", range(7))
def test_attempt_fixtures(index):
    assert m.DailyGameAttempt.model_validate(PLAY["attempts"][index]).model_dump(mode="json")==PLAY["attempts"][index]


@pytest.mark.parametrize("field,value", [("elapsed_ms",0),("awarded_points_units",1875),("score_percentage_bps",7500),("accuracy_bps",0),("is_correct",False),("state","active"),("answer_mode","assisted_mcq"),("question_count",5),("completed_at","2099-12-30T00:00:00.000Z"),("started_at","2099-12-29T12:01:00.000Z"),("choices_revealed",True),("max_points_units",True)])
def test_attempt_inconsistent_telemetry_rejected(field,value):
    raw=deepcopy(PLAY["attempts"][0]);raw[field]=value
    with pytest.raises(ValidationError): m.DailyGameAttempt.model_validate(raw)


@pytest.mark.parametrize("case", ["active","choices_revealed","expired"])
def test_nonterminal_attempt_no_final_data(case):
    raw=active_attempt();raw["state"]=case
    if case=="choices_revealed": raw.update(choices_revealed=True,choices_revealed_at="2099-12-29T12:00:02.000Z",available_points_units=1875)
    assert m.DailyGameAttempt.model_validate(raw).state==case
    raw["awarded_points_units"]=0
    with pytest.raises(ValidationError):m.DailyGameAttempt.model_validate(raw)


def test_timestamp_comparison_uses_instant_not_string_sort():
    # Omitted milliseconds vs explicit milliseconds must not change chronological order.
    fast=deepcopy(PLAY["attempts"][0]); fast["completed_at"]="2099-12-29T12:00:40Z"
    a=m.DailyGameAttempt.model_validate(fast);b=m.DailyGameAttempt.model_validate(PLAY["attempts"][0])
    assert game_ranking_key(a)==game_ranking_key(b)


def test_trivia_duplicate_submitted_ids_rejected():
    raw=deepcopy(PLAY["attempts"][4]);raw["final_answer"]["answers"][1]["question_id"]="q0"
    with pytest.raises(ValidationError):m.DailyGameAttempt.model_validate(raw)


@pytest.mark.parametrize("field,value", [("total_points_units",8874),("total_elapsed_ms",160000),("completed_game_count",3),("overall_eligible",False),("completed_at",None),("state","in_progress")])
def test_daily_result_invariants(field,value):
    raw=deepcopy(PLAY["daily_result"]);raw[field]=value
    with pytest.raises(ValidationError):m.DailyResult.model_validate(raw)


@pytest.mark.parametrize("case", ["duplicate_game","duplicate_position","wrong_date","fractional_midnight","wrong_close","missing_game","wrong_policy","fake_fallback"])
def test_challenge_invariants(case):
    raw=deepcopy(CONTENT["challenge"])
    if case=="duplicate_game":raw["games"][1]["id"]=raw["games"][0]["id"]
    elif case=="duplicate_position":raw["games"][1]["position"]=1
    elif case=="wrong_date":raw["date"]="2099-12-28"
    elif case=="fractional_midnight":raw["opens_at"]="2099-12-29T00:00:00.000001Z";raw["closes_at"]="2099-12-30T00:00:00.000001Z"
    elif case=="wrong_close":raw["closes_at"]="2099-12-30T01:00:00.000Z"
    elif case=="missing_game":raw["games"].pop()
    elif case=="wrong_policy":raw["games"][2]["answer_policy"]["assisted_cap_bps"]=7500
    elif case=="fake_fallback":raw["is_fallback"]=True
    with pytest.raises(ValidationError):m.DailyChallenge.model_validate(raw)


@pytest.mark.parametrize("field,value", [("date","2099-12-28"),("longest_streak_after",1),("source_attempt_id",None),("legacy_score_id",PLAY["attempts"][0]["id"])])
def test_activity_invariants(field,value):
    raw=deepcopy(PLAY["daily_activity"]);raw[field]=value
    with pytest.raises(ValidationError):m.DailyActivity.model_validate(raw)


def test_game_rank_accuracy_then_time():
    attempts=[m.DailyGameAttempt.model_validate(a) for a in PLAY["attempts"]]
    assert [a.elapsed_ms for a in sorted(attempts[:4],key=game_ranking_key)]==[40000,60000,8000,5000]
    assert [(a.correct_count,a.elapsed_ms) for a in sorted(attempts[4:],key=game_ranking_key)]==[(5,60000),(5,90000),(4,8000)]


def test_rank_performance_tie_ignores_uuid_and_completion_timestamp():
    a=m.DailyGameAttempt.model_validate(PLAY["attempts"][0]);raw=deepcopy(PLAY["attempts"][0])
    raw.update(id="ffffffff-ffff-4fff-8fff-ffffffffffff",started_at="2099-12-29T13:00:00.000Z",completed_at="2099-12-29T13:00:40.000Z")
    b=m.DailyGameAttempt.model_validate(raw)
    assert game_performance_key(a)==game_performance_key(b)
    assert game_ranking_key(a)<game_ranking_key(b)


def test_incomplete_attempt_and_day_not_rank_eligible():
    with pytest.raises(ValueError):game_ranking_key(m.DailyGameAttempt.model_validate(active_attempt()))
    raw=deepcopy(PLAY["daily_result"])
    raw.update(state="in_progress",completed_game_count=0,total_points_units=0,total_elapsed_ms=0,completed_at=None,overall_eligible=False,games=[])
    with pytest.raises(ValueError):overall_ranking_key(m.DailyResult.model_validate(raw))


def test_overall_order_points_before_elapsed():
    raw=deepcopy(PLAY["daily_result"]);slow=m.DailyResult.model_validate(raw)
    raw["games"][0]["awarded_points_units"]=1875;raw["games"][0]["score_percentage_bps"]=7500;raw["games"][0]["answer_mode"]="assisted_mcq"
    raw["games"][0]["elapsed_ms"]=1;raw["total_points_units"]-=625;raw["total_elapsed_ms"]-=39999
    fast=m.DailyResult.model_validate(raw)
    assert overall_ranking_key(slow)<overall_ranking_key(fast)


@pytest.mark.parametrize("n,better,tied,bps,top", [(99,0,1,None,None),(100,0,1,9900,1),(1000,60,10,9300,7),(100,0,100,0,100),(100,99,1,0,100),(10000,0,1,9999,1)])
def test_percentile_full_population_and_ties(n,better,tied,bps,top):
    result=full_population_percentile(eligible_population=n,strictly_better_count=better,tied_performance_count=tied)
    assert (result.percentile_bps,result.top_percent_display)==(bps,top)


@pytest.mark.parametrize("n,better,tied", [(0,0,0),(100,-1,1),(100,100,1),(100,0,0),(100,50,51),(True,0,1),(100.0,0,1)])
def test_percentile_rejects_invalid_counts(n,better,tied):
    with pytest.raises(ValueError):full_population_percentile(eligible_population=n,strictly_better_count=better,tied_performance_count=tied)


def test_small_cohort_percentile_cannot_be_faked():
    raw=deepcopy(PLAY["game_rank_summary"]);raw.update(percentile_bps=9900,top_percent_display=1)
    with pytest.raises(ValidationError):m.GameRankStatSummary.model_validate(raw)


@pytest.mark.parametrize("field,value", [("completed_game_count",-1),("overall_rank",0)])
def test_nullable_history_numeric_constraints_preserved(field,value):
    raw=deepcopy(PLAY["history"][1]);raw[field]=value
    with pytest.raises(ValidationError):m.HistoryRow.model_validate(raw)


def test_query_bounds_and_strict_inputs():
    assert m.LeaderboardQuery().limit==50 and m.HistoryQuery().limit==30
    for value in [0,101,True,"50"]:
        with pytest.raises(ValidationError):m.LeaderboardQuery(limit=value)


def test_module_has_no_application_or_external_client_imports():
    for path in (ROOT/"app/v2").glob("*.py"):
        tree=ast.parse(path.read_text())
        for node in ast.walk(tree):
            if isinstance(node,ast.Import):names=[a.name for a in node.names]
            elif isinstance(node,ast.ImportFrom):names=[node.module or ""]
            else:continue
            assert not any(n.startswith(("app.config","app.database","supabase","openai","httpx","requests","fastapi")) for n in names),path


def test_clean_process_import_and_validation_without_clients_or_network():
    script='''
import importlib.abc, socket, sys
class BlockClients(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname in ('app.config','app.database','supabase','openai','httpx','requests','fastapi'):
            raise AssertionError('Forbidden import: '+fullname)
sys.meta_path.insert(0,BlockClients())
def blocked(*args,**kwargs): raise AssertionError('Network forbidden')
socket.socket.connect=blocked
socket.getaddrinfo=blocked
from app.v2.models import GameContentRevision
from app.v2.scoring import typed_game_points
assert typed_game_points(correct=True,choices_revealed=False)==2500
print('pure import passed')
'''
    result=subprocess.run([sys.executable,"-B","-c",script],cwd=ROOT,text=True,capture_output=True,check=True)
    assert result.stdout.strip()=="pure import passed"
