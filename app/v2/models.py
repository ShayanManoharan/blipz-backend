"""Strict V2 domain/wire shapes. Imports no V1 config, database or networking.

Shape source: docs/v2-contracts/contract.schema.json. Relational/semantic checks
are pure functions in validation.py. These models do not implement persistence.
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Annotated, Literal, get_args, get_origin
from uuid import UUID

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, model_validator


def _uuid(value: str) -> str:
    if str(UUID(value)) != value:
        raise ValueError("UUID must use canonical lowercase hyphenated form")
    return value


def _day(value: str) -> str:
    if date.fromisoformat(value).isoformat() != value:
        raise ValueError("Date must be ISO YYYY-MM-DD")
    return value


def _timestamp(value: str) -> str:
    if not value.endswith("Z") or "T" not in value:
        raise ValueError("Timestamp must be an explicit UTC ISO timestamp ending Z")
    parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    if parsed.tzinfo is None:
        raise ValueError("Timestamp requires UTC timezone")
    return value


UUIDText = Annotated[str, AfterValidator(_uuid)]
UTCDate = Annotated[str, AfterValidator(_day)]
UTCTimestamp = Annotated[str, AfterValidator(_timestamp)]


class DomainModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    @model_validator(mode="before")
    @classmethod
    def reject_literal_type_coercion(cls, values):
        if isinstance(values, dict):
            for name, field in cls.model_fields.items():
                if name in values and get_origin(field.annotation) is Literal:
                    allowed = get_args(field.annotation)
                    if not any(type(values[name]) is type(v) and values[name] == v for v in allowed):
                        raise ValueError(f"Invalid literal value/type for {name}")
        return values

    @model_validator(mode="after")
    def reject_blank_text(self):
        for name in type(self).model_fields:
            value = getattr(self, name)
            if isinstance(value, str) and not value.strip():
                raise ValueError(f"{name} must not be blank")
        return self



class Choice(DomainModel):
    id: Literal['A', 'B', 'C', 'D']
    text: str = Field(min_length=1)


class RuleChoice(DomainModel):
    id: Literal['A', 'B', 'C', 'D']
    text: str = Field(min_length=1)
    rule_spec: RuleSpec


class RuleSpec(DomainModel):
    family: Literal['integer_divisible_by']
    divisor: int = Field(ge=2, le=99)


class NormalizationPolicy(DomainModel):
    version: Literal['answer-normalization-v1']
    unicode: Literal['NFKC']
    casefold: Literal[True]
    collapse_whitespace: Literal[True]
    strip_terminal_punctuation: Literal[True]


class AnswerAliases(DomainModel):
    normalization: NormalizationPolicy
    accepted_aliases: list[str] = Field(min_length=1)
    approved_patterns: list[str] = Field(min_length=0)


class AssetReference(DomainModel):
    asset_id: UUIDText
    uri: str = Field(min_length=1)
    media_type: Literal['image/svg+xml', 'image/png', 'image/jpeg']
    sha256: str = Field(pattern='^[0-9a-f]{64}$')
    license: str = Field(min_length=1)
    attribution: Annotated[str, Field(min_length=1)] | None
    alt_text: str = Field(min_length=1)


class HiddenRulePublicPayload(DomainModel):
    kind: Literal['hidden_rule']
    title: str = Field(min_length=1)
    domain: IntegerDomain
    belongs: list[int] = Field(min_length=3)
    does_not_belong: list[int] = Field(min_length=3)
    answer_instruction: str = Field(min_length=1)


class HiddenRulePrivateSolution(DomainModel):
    kind: Literal['hidden_rule']
    rule_spec: RuleSpec
    canonical_answer: str = Field(min_length=1)
    canonical_explanation: str = Field(min_length=1)
    answers: AnswerAliases
    choices: list[RuleChoice] = Field(min_length=4, max_length=4)
    correct_choice_id: Literal['A', 'B', 'C', 'D']


class RiddlePublicPayload(DomainModel):
    kind: Literal['riddle']
    title: str = Field(min_length=1)
    riddle: str = Field(min_length=1)
    answer_instruction: str = Field(min_length=1)


class RiddlePrivateSolution(DomainModel):
    kind: Literal['riddle']
    canonical_answer: str = Field(min_length=1)
    canonical_explanation: str = Field(min_length=1)
    answers: AnswerAliases
    choices: list[Choice] = Field(min_length=4, max_length=4)
    correct_choice_id: Literal['A', 'B', 'C', 'D']


class TriviaQuestion(DomainModel):
    id: str = Field(min_length=1)
    question: str = Field(min_length=1)
    category: str = Field(min_length=1)
    choices: list[Choice] = Field(min_length=4, max_length=4)


class TriviaPublicPayload(DomainModel):
    kind: Literal['trivia']
    title: str = Field(min_length=1)
    questions: list[TriviaQuestion] = Field(min_length=5, max_length=5)


class TriviaQuestionSolution(DomainModel):
    question_id: str = Field(min_length=1)
    correct_choice_id: Literal['A', 'B', 'C', 'D']
    explanation: str = Field(min_length=1)
    source_note: str = Field(min_length=1)


class TriviaPrivateSolution(DomainModel):
    kind: Literal['trivia']
    questions: list[TriviaQuestionSolution] = Field(min_length=5, max_length=5)


class WorldGuessPublicPayload(DomainModel):
    kind: Literal['world_guess']
    variant: Literal['flag', 'outline']
    title: str = Field(min_length=1)
    asset: AssetReference
    answer_instruction: str = Field(min_length=1)


class WorldGuessPrivateSolution(DomainModel):
    kind: Literal['world_guess']
    entity_id: str = Field(min_length=1)
    dataset_version: str = Field(min_length=1)
    canonical_answer: str = Field(min_length=1)
    canonical_explanation: str = Field(min_length=1)
    answers: AnswerAliases
    choices: list[Choice] = Field(min_length=4, max_length=4)
    correct_choice_id: Literal['A', 'B', 'C', 'D']
    source_note: str = Field(min_length=1)


class ValidationMetadata(DomainModel):
    validator_version: str = Field(min_length=1)
    validated_at: Annotated[UTCTimestamp, Field(pattern='Z$')] | None
    checks: list[str] = Field(min_length=0)
    human_approval: Literal['not_reviewed', 'approved', 'rejected']
    approved_by: Annotated[str, Field(min_length=1)] | None
    approved_at: Annotated[UTCTimestamp, Field(pattern='Z$')] | None
    content_sha256: str = Field(pattern='^[0-9a-f]{64}$')


class GameContentRevision(DomainModel):
    id: UUIDText
    game_type: Literal['hidden_rule', 'riddle', 'trivia', 'world_guess']
    variant: Annotated[str, Field(min_length=1)] | None
    schema_version: Literal[1]
    generator_version: str = Field(min_length=1)
    created_at: UTCTimestamp = Field(pattern='Z$')
    status: Literal['draft', 'validated', 'approved', 'retired']
    public_payload: PublicPayload
    private_solution: PrivateSolution
    validation: ValidationMetadata

    @model_validator(mode="after")
    def check_invariants(self) -> GameContentRevision:
        from .validation import validate_revision

        validate_revision(self)
        return self


class PublicGameContentRevision(DomainModel):
    id: UUIDText
    game_type: Literal['hidden_rule', 'riddle', 'trivia', 'world_guess']
    variant: Annotated[str, Field(min_length=1)] | None
    schema_version: Literal[1]
    public_payload: PublicPayload


class AnswerPolicy(DomainModel):
    mode: Literal['typed_or_optional_choices', 'native_mcq_set']
    final_submissions: Literal[1]
    choice_count: Literal[4]
    assisted_cap_bps: Literal[7500, 10000]
    hints_enabled: Literal[False]


class DailyGameInstance(DomainModel):
    id: UUIDText
    challenge_id: UUIDText
    position: int = Field(ge=1)
    game_type: Literal['hidden_rule', 'riddle', 'trivia', 'world_guess']
    variant: Annotated[str, Field(min_length=1)] | None
    content_revision_id: UUIDText
    title: str = Field(min_length=1)
    presentation_role: Literal['hero', 'compact']
    max_points_units: Literal[2500]
    answer_policy: AnswerPolicy

    @model_validator(mode="after")
    def check_invariants(self) -> DailyGameInstance:
        from .validation import validate_instance

        validate_instance(self)
        return self


class DailyChallenge(DomainModel):
    id: UUIDText
    date: UTCDate
    product_version: Literal['blipz-v2']
    contract_version: Literal['v2.1']
    scoring_version: Literal['v2-equal25-v1']
    ranking_version: Literal['v2-rank-v1']
    lineup_revision: UUIDText
    status: Literal['published']
    opens_at: UTCTimestamp = Field(pattern='Z$')
    closes_at: UTCTimestamp = Field(pattern='Z$')
    published_at: UTCTimestamp = Field(pattern='Z$')
    required_game_count: Literal[4]
    max_points_units: Literal[10000]
    is_fallback: bool
    fallback_source_id: UUIDText | None
    games: list[DailyGameInstance] = Field(min_length=4, max_length=4)

    @model_validator(mode="after")
    def check_invariants(self) -> DailyChallenge:
        from .validation import validate_challenge

        validate_challenge(self)
        return self


class TriviaSelection(DomainModel):
    question_id: str = Field(min_length=1)
    selected_option_id: Literal['A', 'B', 'C', 'D']


class DailyGameAttempt(DomainModel):
    id: UUIDText
    user_id: UUIDText
    challenge_id: UUIDText
    game_instance_id: UUIDText
    content_revision_id: UUIDText
    game_type: Literal['hidden_rule', 'riddle', 'trivia', 'world_guess']
    challenge_date: UTCDate
    state: Literal['active', 'choices_revealed', 'completed_correct', 'completed_incorrect', 'expired']
    started_at: UTCTimestamp = Field(pattern='Z$')
    choices_revealed_at: Annotated[UTCTimestamp, Field(pattern='Z$')] | None
    completed_at: Annotated[UTCTimestamp, Field(pattern='Z$')] | None
    elapsed_ms: Annotated[int, Field(ge=0)] | None
    clock_source: Literal['database_wall_clock']
    timing_version: Literal['wall-clock-v1']
    choices_revealed: bool
    answer_mode: Literal['typed', 'assisted_mcq', 'native_mcq'] | None
    final_answer: FinalAnswer | None
    correct_count: Annotated[int, Field(ge=0)] | None
    question_count: Annotated[int, Field(ge=1)] | None
    is_correct: bool | None
    accuracy_bps: Annotated[int, Field(ge=0, le=10000)] | None
    max_points_units: Literal[2500]
    available_points_units: Literal[2500, 1875]
    awarded_points_units: Annotated[int, Field(ge=0, le=2500)] | None
    score_percentage_bps: Annotated[int, Field(ge=0, le=10000)] | None
    scoring_version: Literal['v2-equal25-v1']
    grading_version: str = Field(min_length=1)
    completion_idempotency_key: UUIDText | None

    @model_validator(mode="after")
    def check_invariants(self) -> DailyGameAttempt:
        from .validation import validate_attempt

        validate_attempt(self)
        return self


class GameResult(DomainModel):
    game_instance_id: UUIDText
    attempt_id: UUIDText
    game_type: Literal['hidden_rule', 'riddle', 'trivia', 'world_guess']
    state: Literal['completed_correct', 'completed_incorrect']
    awarded_points_units: int = Field(ge=0)
    max_points_units: Literal[2500]
    elapsed_ms: int = Field(ge=0)
    answer_mode: Literal['typed', 'assisted_mcq', 'native_mcq']
    accuracy_bps: int = Field(ge=0, le=10000)
    score_percentage_bps: int = Field(ge=0, le=10000)

    @model_validator(mode="after")
    def check_invariants(self) -> GameResult:
        from .validation import validate_game_result

        validate_game_result(self)
        return self


class DailyResult(DomainModel):
    id: UUIDText
    user_id: UUIDText
    challenge_id: UUIDText
    date: UTCDate
    product_version: Literal['blipz-v2']
    scoring_version: Literal['v2-equal25-v1']
    state: Literal['in_progress', 'completed']
    completed_game_count: int = Field(ge=0, le=4)
    required_game_count: Literal[4]
    total_points_units: int = Field(ge=0, le=10000)
    max_points_units: Literal[10000]
    total_elapsed_ms: int = Field(ge=0)
    completed_at: Annotated[UTCTimestamp, Field(pattern='Z$')] | None
    overall_eligible: bool
    games: list[GameResult] = Field(min_length=0, max_length=4)

    @model_validator(mode="after")
    def check_invariants(self) -> DailyResult:
        from .validation import validate_result

        validate_result(self)
        return self


class DailyActivity(DomainModel):
    user_id: UUIDText
    date: UTCDate
    first_completed_at: UTCTimestamp = Field(pattern='Z$')
    source_product: Literal['blipz-v1', 'blipz-v2']
    source_attempt_id: UUIDText | None
    legacy_score_id: UUIDText | None
    streak_rule_version: Literal['first-completion-utc-v1']
    streak_after: int = Field(ge=1)
    longest_streak_after: int = Field(ge=1)

    @model_validator(mode="after")
    def check_invariants(self) -> DailyActivity:
        from .validation import validate_activity

        validate_activity(self)
        return self


class ReviewItem(DomainModel):
    question_id: Annotated[str, Field(min_length=1)] | None
    submitted_answer: Annotated[str, Field(min_length=1)] | None
    correct_answer: str = Field(min_length=1)
    is_correct: bool
    explanation: str = Field(min_length=1)


class CompletedGameReview(DomainModel):
    challenge_id: UUIDText
    game_instance_id: UUIDText
    content_revision_id: UUIDText
    attempt_id: UUIDText
    date: UTCDate
    game_type: Literal['hidden_rule', 'riddle', 'trivia', 'world_guess']
    result: GameResult
    public_payload: PublicPayload
    canonical_answer: Annotated[str, Field(min_length=1)] | None
    explanation: str = Field(min_length=1)
    items: list[ReviewItem] = Field(min_length=0)

    @model_validator(mode="after")
    def check_invariants(self) -> CompletedGameReview:
        from .validation import validate_review

        validate_review(self)
        return self


class Player(DomainModel):
    player_id: UUIDText
    username: Annotated[str, Field(min_length=1)] | None


class OverallLeaderboardRow(DomainModel):
    rank: int = Field(ge=1)
    performance_rank: int = Field(ge=1)
    player: Player
    challenge_id: UUIDText
    total_points_units: int = Field(ge=0)
    max_points_units: Literal[10000]
    total_elapsed_ms: int = Field(ge=0)
    completed_at: UTCTimestamp = Field(pattern='Z$')


class GameLeaderboardRow(DomainModel):
    rank: int = Field(ge=1)
    performance_rank: int = Field(ge=1)
    player: Player
    challenge_id: UUIDText
    game_instance_id: UUIDText
    game_type: Literal['hidden_rule', 'riddle', 'trivia', 'world_guess']
    awarded_points_units: int = Field(ge=0)
    max_points_units: Literal[2500]
    score_percentage_bps: int = Field(ge=0, le=10000)
    accuracy_bps: int = Field(ge=0, le=10000)
    correct_count: int = Field(ge=0)
    question_count: int = Field(ge=1)
    choices_revealed: bool
    answer_mode: Literal['typed', 'assisted_mcq', 'native_mcq']
    elapsed_ms: int = Field(ge=0)
    completed_at: UTCTimestamp = Field(pattern='Z$')


class GameRankStatSummary(DomainModel):
    challenge_id: UUIDText
    game_instance_id: UUIDText
    player_id: UUIDText
    as_of: UTCTimestamp = Field(pattern='Z$')
    ranking_version: Literal['v2-rank-v1']
    eligible: bool
    rank: Annotated[int, Field(ge=1)] | None
    performance_rank: Annotated[int, Field(ge=1)] | None
    eligible_population: int = Field(ge=0)
    strictly_better_count: Annotated[int, Field(ge=0)] | None
    tied_performance_count: Annotated[int, Field(ge=1)] | None
    percentile_bps: Annotated[int, Field(ge=0, le=10000)] | None
    top_percent_display: Annotated[int, Field(ge=1, le=100)] | None
    minimum_population: Literal[100]
    percentile_suppression_reason: Literal['not_completed', 'population_below_100'] | None

    @model_validator(mode="after")
    def check_invariants(self) -> GameRankStatSummary:
        from .validation import validate_rank_summary

        validate_rank_summary(self)
        return self


class HistoryRow(DomainModel):
    date: UTCDate
    product_version: Literal['blipz-v1', 'blipz-v2']
    challenge_id: UUIDText | None
    scoring_version: Literal['legacy_raw_35', 'normalized_100', 'v2-equal25-v1']
    total_points_units: int = Field(ge=0)
    max_points_units: Literal[3500, 10000]
    completed_game_count: Annotated[int, Field(ge=0)] | None
    required_game_count: int = Field(ge=1)
    completed: bool | None
    overall_rank: Annotated[int, Field(ge=1)] | None
    games: list[GameResult] = Field(min_length=0)
    legacy_score_id: UUIDText | None


class ProgressSlot(DomainModel):
    game_instance_id: UUIDText
    state: Literal['not_started', 'active', 'choices_revealed', 'completed_correct', 'completed_incorrect', 'expired']
    attempt_id: UUIDText | None
    started_at: Annotated[UTCTimestamp, Field(pattern='Z$')] | None
    choices_revealed: bool
    available_points_units: Literal[2500, 1875]
    result: GameResult | None


class DailyProgress(DomainModel):
    challenge_id: UUIDText
    date: UTCDate
    as_of: UTCTimestamp = Field(pattern='Z$')
    games: list[ProgressSlot] = Field(min_length=4, max_length=4)
    daily_result: DailyResult
    activity_recorded: bool
    current_streak: int = Field(ge=0)
    longest_streak: int = Field(ge=0)


class StartAttemptResponse(DomainModel):
    attempt: DailyGameAttempt
    content: PublicGameContentRevision
    server_time: UTCTimestamp = Field(pattern='Z$')
    closes_at: UTCTimestamp = Field(pattern='Z$')
    already_started: bool
    already_completed: bool


class RevealChoicesResponse(DomainModel):
    attempt_id: UUIDText
    game_instance_id: UUIDText
    choices: list[Choice] = Field(min_length=4, max_length=4)
    choices_revealed_at: UTCTimestamp = Field(pattern='Z$')
    available_points_units: Literal[1875]
    already_revealed: bool


class SubmitResponse(DomainModel):
    attempt: DailyGameAttempt
    daily_result: DailyResult
    already_completed: bool
    review_available: Literal[True]
    activity_created: bool
    current_streak: int = Field(ge=0)
    longest_streak: int = Field(ge=0)


class Error(DomainModel):
    error: ErrorDetails


class IntegerDomain(DomainModel):
    type: Literal['integer']
    minimum: int
    maximum: int


class TypedFinalAnswer(DomainModel):
    kind: Literal['typed']
    text: str = Field(min_length=1, max_length=300)


class ChoiceFinalAnswer(DomainModel):
    kind: Literal['choice']
    selected_option_id: Literal['A', 'B', 'C', 'D']


class TriviaFinalAnswer(DomainModel):
    kind: Literal['trivia']
    answers: list[TriviaSelection] = Field(min_length=5, max_length=5)


class ErrorDetails(DomainModel):
    code: str = Field(min_length=1)
    message: str = Field(min_length=1)
    retryable: bool
    request_id: UUIDText

PublicPayload = Annotated[HiddenRulePublicPayload | RiddlePublicPayload | TriviaPublicPayload | WorldGuessPublicPayload, Field(discriminator="kind")]

PrivateSolution = Annotated[HiddenRulePrivateSolution | RiddlePrivateSolution | TriviaPrivateSolution | WorldGuessPrivateSolution, Field(discriminator="kind")]

FinalAnswer = Annotated[TypedFinalAnswer | ChoiceFinalAnswer | TriviaFinalAnswer, Field(discriminator="kind")]



class MutationRequest(DomainModel):
    idempotency_key: UUIDText


class SubmitRequest(MutationRequest):
    answer: FinalAnswer



class ChallengeResponse(DomainModel):
    challenge: DailyChallenge
    server_time: UTCTimestamp



class ResultResponse(DomainModel):
    result: DailyResult
    server_time: UTCTimestamp



class OverallLeaderboardResponse(DomainModel):
    challenge_id: UUIDText
    ranking_version: Literal["v2-rank-v1"]
    as_of: UTCTimestamp
    eligible_population: int = Field(ge=0)
    rows: list[OverallLeaderboardRow]
    self: OverallLeaderboardRow | None
    next_cursor: str | None



class GameLeaderboardResponse(DomainModel):
    challenge_id: UUIDText
    game_instance_id: UUIDText
    ranking_version: Literal["v2-rank-v1"]
    as_of: UTCTimestamp
    eligible_population: int = Field(ge=0)
    rows: list[GameLeaderboardRow]
    self: GameLeaderboardRow | None
    next_cursor: str | None



class HistoryResponse(DomainModel):
    rows: list[HistoryRow]
    next_cursor: str | None



class LeaderboardQuery(DomainModel):
    limit: int = Field(default=50, ge=1, le=100)
    cursor: str | None = Field(default=None, min_length=1, max_length=4096)



class HistoryQuery(DomainModel):
    limit: int = Field(default=30, ge=1, le=100)
    cursor: str | None = Field(default=None, min_length=1, max_length=4096)


# Resolve the explicit forward references after discriminated unions exist.
for _candidate in tuple(globals().values()):
    if isinstance(_candidate, type) and issubclass(_candidate, DomainModel):
        _candidate.model_rebuild()
del _candidate
