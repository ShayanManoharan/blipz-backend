"""Pure ordering/math only; callers must supply the full eligible population."""
from dataclasses import dataclass
from .models import DailyGameAttempt, DailyResult

RANKING_VERSION = "v2-rank-v1"
MIN_PERCENTILE_POPULATION = 100
TERMINAL_STATES = frozenset({"completed_correct", "completed_incorrect"})


def game_performance_key(attempt: DailyGameAttempt) -> tuple:
    if attempt.state not in TERMINAL_STATES:
        raise ValueError("Only completed attempts are rank eligible")
    if attempt.game_type == "trivia":
        return (-attempt.correct_count, attempt.elapsed_ms)
    return (-attempt.awarded_points_units, attempt.choices_revealed, attempt.elapsed_ms)


def game_ranking_key(attempt: DailyGameAttempt) -> tuple:
    from .validation import utc_timestamp

    return game_performance_key(attempt) + (utc_timestamp(attempt.completed_at), attempt.id)


def overall_performance_key(result: DailyResult) -> tuple:
    if not result.overall_eligible or result.state != "completed":
        raise ValueError("Overall ranking requires all daily games completed")
    return (-result.total_points_units, result.total_elapsed_ms)


def overall_ranking_key(result: DailyResult) -> tuple:
    from .validation import utc_timestamp

    return overall_performance_key(result) + (utc_timestamp(result.completed_at), result.id)


@dataclass(frozen=True)
class Percentile:
    percentile_bps: int | None
    top_percent_display: int | None


def full_population_percentile(*, eligible_population: int, strictly_better_count: int, tied_performance_count: int) -> Percentile:
    """Explicit full-cohort counts, never a page of leaderboard rows.

    This function cannot verify a caller's database population; future queries must
    compute these counts at one consistent snapshot over the complete cohort.
    """
    n, better, tied = eligible_population, strictly_better_count, tied_performance_count
    if any(type(v) is not int for v in (n, better, tied)) or n <= 0 or better < 0 or tied < 1 or better + tied > n:
        raise ValueError("Invalid full-population counts")
    if n < MIN_PERCENTILE_POPULATION:
        return Percentile(None, None)
    return Percentile(10000 * (n - better - tied) // n, (100 * (better + tied) + n - 1) // n)
