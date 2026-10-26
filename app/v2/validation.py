"""Pure semantic validation. No database, clock reads, filesystem or network I/O."""
import hashlib
import json
from datetime import datetime, timedelta
from urllib.parse import unquote, urlsplit

from . import models as m
from .answers import matches_alias, normalize_answer
from .scoring import percentage_bps, trivia_points, typed_game_points

TERMINAL = frozenset({"completed_correct", "completed_incorrect"})
GAMES = ("hidden_rule", "riddle", "trivia", "world_guess")


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def utc_timestamp(value: str) -> datetime:
    return datetime.fromisoformat(value[:-1] + "+00:00")


def duration_ms(start: str, end: str) -> int:
    delta = utc_timestamp(end) - utc_timestamp(start)
    require(delta >= timedelta(0), "Completion precedes start")
    return delta.days * 86400000 + delta.seconds * 1000 + delta.microseconds // 1000


def validate_aliases(answers: m.AnswerAliases, canonical: str) -> None:
    require(not answers.approved_patterns, "Pattern execution is not supported; approved aliases only")
    normalized = [normalize_answer(s) for s in answers.accepted_aliases]
    require(all(normalized), "Answer aliases must not normalize to empty text")
    require(len(normalized) == len(set(normalized)), "Duplicate normalized answer aliases")
    require(matches_alias(canonical, answers.accepted_aliases), "Canonical answer must have an approved alias")


def validate_choices(choices: list, correct_id: str) -> None:
    require(len(choices) == 4 and {c.id for c in choices} == set("ABCD"), "Exactly four distinct A-D choices required")
    labels = [normalize_answer(c.text) for c in choices]
    require(all(labels) and len(set(labels)) == 4, "Choice labels must be nonempty and distinct")
    require(correct_id in {c.id for c in choices}, "Unknown correct choice ID")


def validate_answer_choices(private) -> None:
    validate_aliases(private.answers, private.canonical_answer)
    validate_choices(private.choices, private.correct_choice_id)
    matching = [c.id for c in private.choices if matches_alias(c.text, private.answers.accepted_aliases)]
    require(matching == [private.correct_choice_id], "Exactly the correct choice label must match approved aliases")


def validate_riddle(public: m.RiddlePublicPayload, private: m.RiddlePrivateSolution) -> None:
    require(bool(public.riddle.strip()), "Riddle text must not be blank")
    validate_answer_choices(private)


def validate_trivia(public: m.TriviaPublicPayload, private: m.TriviaPrivateSolution) -> None:
    ids = [q.id for q in public.questions]
    require(len(ids) == 5 and len(set(ids)) == 5, "Five distinct trivia questions required")
    require(len({normalize_answer(q.question) for q in public.questions}) == 5, "Duplicate trivia question text")
    solutions = {q.question_id: q for q in private.questions}
    require(len(solutions) == 5 and set(solutions) == set(ids), "Trivia solution IDs must match public questions exactly")
    for question in public.questions:
        require(bool(question.category.strip()), "Trivia category must not be blank")
        validate_choices(question.choices, solutions[question.id].correct_choice_id)


def validate_world_guess(public: m.WorldGuessPublicPayload, private: m.WorldGuessPrivateSolution, *, allow_local_assets: bool = False) -> None:
    validate_answer_choices(private)
    asset = public.asset
    parsed = urlsplit(asset.uri)
    https = parsed.scheme == "https" and bool(parsed.hostname) and not parsed.username and not parsed.password and not parsed.fragment
    local = allow_local_assets and parsed.scheme == "local-fixture" and parsed.netloc == "assets" and parsed.path in ("/visual-a.svg", "/visual-b.svg")
    require(https or local, "Asset URI must be immutable HTTPS; local-fixture is allowed only in explicit offline examples")
    metadata = normalize_answer(unquote(asset.uri) + " " + asset.alt_text + " " + (asset.attribution or ""))
    # Obvious metadata leakage check, not a substitute for editorial asset review.
    for alias in private.answers.accepted_aliases:
        alias = normalize_answer(alias)
        if len(alias) >= 3:
            require(alias not in metadata, "Asset metadata reveals an accepted answer")
    require(bool(private.entity_id.strip()) and bool(private.dataset_version.strip()), "Canonical entity and dataset version required")


def validate_revision(revision: m.GameContentRevision) -> None:
    public, private = revision.public_payload, revision.private_solution
    require(public.kind == private.kind == revision.game_type, "Revision/public/private game types differ")
    require(revision.variant == (public.variant if isinstance(public, m.WorldGuessPublicPayload) else None), "Revision variant mismatch")
    if revision.game_type == "hidden_rule":
        from .rules import validate_hidden_rule

        validate_hidden_rule(public, private)
    elif revision.game_type == "riddle":
        validate_riddle(public, private)
    elif revision.game_type == "trivia":
        validate_trivia(public, private)
    else:
        local_example = revision.generator_version == "local-handwritten-fixture-v1" and revision.validation.human_approval == "not_reviewed" and revision.status != "approved"
        validate_world_guess(public, private, allow_local_assets=local_example)
    payload = {"public_payload": public.model_dump(mode="json"), "private_solution": private.model_dump(mode="json")}
    digest = hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()
    require(digest == revision.validation.content_sha256, "Content hash does not match revision payload")
    v = revision.validation
    if revision.status in ("validated", "approved"):
        require(v.validated_at is not None and bool(v.checks), "Validated content requires validation evidence")
    if v.human_approval == "approved":
        require(bool(v.approved_by) and v.approved_at is not None, "Human approval requires actor and timestamp")
    else:
        require(v.approved_by is None and v.approved_at is None, "Unapproved content cannot claim approval metadata")
    if revision.status == "approved":
        require(v.human_approval == "approved", "Approved status requires editorial approval")


def validate_publishable_revision(revision: m.GameContentRevision) -> None:
    """Pure eligibility gate only; does not publish, fetch or write anything."""
    validate_revision(revision)
    require(revision.status == "approved" and revision.validation.human_approval == "approved", "Publication requires approved content")
    if revision.game_type == "world_guess":
        validate_world_guess(revision.public_payload, revision.private_solution)


def public_revision_view(revision: m.GameContentRevision) -> m.PublicGameContentRevision:
    """Explicit allowlist; private solution and editorial metadata never escape."""
    return m.PublicGameContentRevision(
        id=revision.id,
        game_type=revision.game_type,
        variant=revision.variant,
        schema_version=revision.schema_version,
        public_payload=revision.public_payload,
    )


def validate_instance(instance: m.DailyGameInstance) -> None:
    trivia = instance.game_type == "trivia"
    require(instance.answer_policy.mode == ("native_mcq_set" if trivia else "typed_or_optional_choices"), "Wrong answer policy for game")
    require(instance.answer_policy.assisted_cap_bps == (10000 if trivia else 7500), "Wrong assistance cap for game")
    require(instance.variant in ("flag", "outline") if instance.game_type == "world_guess" else instance.variant is None, "Unsupported game variant")


def validate_challenge(challenge: m.DailyChallenge) -> None:
    require([g.game_type for g in challenge.games] == list(GAMES), "Launch lineup must contain the four games in approved order")
    require([g.position for g in challenge.games] == [1, 2, 3, 4], "Positions must be unique and contiguous")
    require(len({g.id for g in challenge.games}) == 4, "Duplicate game instance ID")
    require(all(g.challenge_id == challenge.id for g in challenge.games), "Game belongs to a different challenge")
    require(sum(g.max_points_units for g in challenge.games) == challenge.max_points_units, "Lineup maximum mismatch")
    start = utc_timestamp(challenge.opens_at)
    require(start == datetime.fromisoformat(challenge.date + "T00:00:00+00:00"), "Challenge must open at its UTC midnight")
    require(utc_timestamp(challenge.closes_at) == start + timedelta(days=1), "Challenge must close next UTC midnight")
    require(challenge.is_fallback == (challenge.fallback_source_id is not None), "Fallback source/flag mismatch")


def validate_attempt(attempt: m.DailyGameAttempt) -> None:
    a = attempt
    require(a.started_at[:10] == a.challenge_date, "Attempt start must be on challenge UTC date")
    require(a.choices_revealed == (a.choices_revealed_at is not None), "Choice state/timestamp mismatch")
    require(a.available_points_units == (1875 if a.choices_revealed else 2500), "Assistance cap mismatch")
    if a.choices_revealed:
        require(a.game_type != "trivia", "Native Trivia cannot reveal assistance")
        require(utc_timestamp(a.choices_revealed_at) >= utc_timestamp(a.started_at), "Choice reveal precedes attempt start")
        require(a.choices_revealed_at[:10] == a.challenge_date, "Choice reveal past UTC deadline")
    final_fields = (a.completed_at, a.elapsed_ms, a.answer_mode, a.final_answer, a.correct_count, a.question_count, a.is_correct, a.accuracy_bps, a.awarded_points_units, a.score_percentage_bps, a.completion_idempotency_key)
    if a.state not in TERMINAL:
        require(all(v is None for v in final_fields), "Unfinished attempt cannot contain final score/answer/timing")
        if a.state != "expired":
            require((a.state == "choices_revealed") == a.choices_revealed, "Active state and assistance differ")
        return
    require(all(v is not None for v in final_fields), "Completed attempt is missing final data")
    require(a.completed_at[:10] == a.challenge_date, "Final answer after UTC deadline")
    require(a.elapsed_ms == duration_ms(a.started_at, a.completed_at), "Elapsed time must equal server timestamp duration")
    if a.choices_revealed:
        require(utc_timestamp(a.choices_revealed_at) <= utc_timestamp(a.completed_at), "Choice reveal after completion")
    require(a.correct_count <= a.question_count, "Correct count exceeds question count")
    require(a.is_correct == (a.correct_count == a.question_count), "Correctness/count mismatch")
    require((a.state == "completed_correct") == a.is_correct, "Terminal state and correctness differ")
    if a.game_type == "trivia":
        require(a.answer_mode == "native_mcq" and not a.choices_revealed and a.question_count == 5 and a.final_answer.kind == "trivia", "Invalid Trivia attempt mode/count")
        require(len({answer.question_id for answer in a.final_answer.answers}) == 5, "Duplicate submitted trivia question IDs")
        expected = trivia_points(a.correct_count)
    else:
        require(a.question_count == 1, "Typed game must have one final answer")
        require(a.answer_mode == ("assisted_mcq" if a.choices_revealed else "typed"), "Final mode differs from irreversible assistance")
        require(a.final_answer.kind == ("choice" if a.choices_revealed else "typed"), "Submitted answer kind differs from mode")
        expected = typed_game_points(correct=a.is_correct, choices_revealed=a.choices_revealed)
    require(a.awarded_points_units == expected, "Awarded points mismatch")
    require(a.accuracy_bps == a.correct_count * 10000 // a.question_count, "Accuracy mismatch")
    require(a.score_percentage_bps == percentage_bps(expected, a.max_points_units), "Awarded percentage mismatch")
    # Final-answer correctness must later be computed against the locked revision;
    # this telemetry model alone cannot authenticate a caller's stored result.


def validate_game_result(result: m.GameResult) -> None:
    require(result.awarded_points_units <= result.max_points_units, "Contribution exceeds game maximum")
    require(result.score_percentage_bps == percentage_bps(result.awarded_points_units, result.max_points_units), "Contribution percentage mismatch")
    require(result.answer_mode == "native_mcq" if result.game_type == "trivia" else result.answer_mode in ("typed", "assisted_mcq"), "Contribution answer mode mismatch")
    require((result.state == "completed_correct") == (result.accuracy_bps == 10000), "Contribution correctness mismatch")
    if result.game_type == "trivia":
        require(result.accuracy_bps % 2000 == 0 and result.awarded_points_units == result.accuracy_bps // 4, "Trivia contribution mismatch")
    else:
        require(result.accuracy_bps in (0, 10000), "Typed game accuracy must be binary")
        require(result.awarded_points_units == typed_game_points(correct=result.accuracy_bps == 10000, choices_revealed=result.answer_mode == "assisted_mcq"), "Typed contribution mismatch")


def validate_result(result: m.DailyResult) -> None:
    games = result.games
    require(len({g.game_instance_id for g in games}) == len(games) and len({g.attempt_id for g in games}) == len(games), "Duplicate result contribution")
    require(len({g.game_type for g in games}) == len(games), "Duplicate launch game type")
    require(result.completed_game_count == len(games), "Completed count mismatch")
    require(result.total_points_units == sum(g.awarded_points_units for g in games), "Daily total mismatch")
    require(result.total_elapsed_ms == sum(g.elapsed_ms for g in games), "Daily elapsed sum mismatch")
    complete = len(games) == result.required_game_count
    require(result.overall_eligible == complete and (result.state == "completed") == complete, "Overall eligibility/state mismatch")
    require((result.completed_at is not None) == complete, "Daily completion timestamp mismatch")
    if complete:
        require(result.completed_at[:10] == result.date, "Daily completion past UTC deadline")


def validate_activity(activity: m.DailyActivity) -> None:
    require(activity.first_completed_at[:10] == activity.date, "Activity must use completion UTC date")
    require(activity.longest_streak_after >= activity.streak_after, "Longest streak below current streak")
    if activity.source_product == "blipz-v2":
        require(activity.source_attempt_id is not None and activity.legacy_score_id is None, "V2 activity requires attempt source only")
    else:
        require(activity.legacy_score_id is not None and activity.source_attempt_id is None, "V1 activity requires legacy source only")


def validate_review(review: m.CompletedGameReview) -> None:
    require(review.game_instance_id == review.result.game_instance_id and review.attempt_id == review.result.attempt_id, "Review result identity mismatch")
    require(review.game_type == review.result.game_type == review.public_payload.kind, "Review game type mismatch")


def validate_rank_summary(summary: m.GameRankStatSummary) -> None:
    from .ranking import full_population_percentile

    s = summary
    if not s.eligible:
        require(all(v is None for v in (s.rank, s.performance_rank, s.strictly_better_count, s.tied_performance_count, s.percentile_bps, s.top_percent_display)), "Ineligible player must not have ranks/percentiles")
        require(s.percentile_suppression_reason == "not_completed", "Incorrect percentile suppression reason")
        return
    require(all(v is not None for v in (s.rank, s.performance_rank, s.strictly_better_count, s.tied_performance_count)), "Eligible player requires exact rank/population counts")
    b, t = s.strictly_better_count, s.tied_performance_count
    require(s.performance_rank == b + 1 and b + 1 <= s.rank <= b + t, "Rank and performance tie counts disagree")
    p = full_population_percentile(eligible_population=s.eligible_population, strictly_better_count=b, tied_performance_count=t)
    require((s.percentile_bps, s.top_percent_display) == (p.percentile_bps, p.top_percent_display), "Full-population percentile mismatch")
    require(s.percentile_suppression_reason == ("population_below_100" if s.eligible_population < 100 else None), "Incorrect percentile suppression")
