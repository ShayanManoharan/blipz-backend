# scoring.py
# Normalized 100-point daily score: Guess 40 / Trivia 30 / Maths 30.
#
# Cutover is date-based, not a per-row flag or migration: any `scores` row dated
# before SCORING_V2_CUTOVER_DATE keeps whatever total_score it was already given by
# the legacy raw-sum formula, forever — never rewritten. Any row dated on or after it
# is computed by compute_total_score below. This works because a row's `date` is fixed
# at creation and every later update to that same row happens through the same code
# path with the same `today` string, so a single day's row can never straddle two
# formulas. See complete_game_attempt in app/routers/games.py, which is the only
# caller, and PRODUCTION_AUDIT.md-equivalent reasoning in this session's report for why
# historical rows are never retroactively recomputed even at the display layer: the
# maths_elapsed_seconds column didn't exist yet when the oldest completed Maths
# attempts were locked in, so the new formula's required input is permanently missing
# for them — there is no "safe" way to normalize those rows after the fact.

from datetime import date

SCORING_V2_CUTOVER_DATE = date(2026, 8, 6)

GUESS_MAX_POINTS = 40.0
TRIVIA_MAX_POINTS = 30.0

GUESS_MAX_RAW = 10.0
TRIVIA_MAX_RAW = 5

# (elapsed_seconds, points), ascending by time. This table IS the entire formula: at or
# under the first breakpoint's time caps at its points, at or over the last breakpoint's
# time floors at its points, and every other point is continuous linear interpolation
# between its neighbors — nothing hidden, nothing bucketed.
MATHS_BREAKPOINTS: list[tuple[float, float]] = [
    (25, 30),
    (30, 28),
    (40, 25),
    (50, 22),
    (60, 19),
    (75, 15),
    (90, 11),
    (120, 6),
    (150, 3),
]


def compute_guess_points(guess_score: float) -> float:
    return (guess_score / GUESS_MAX_RAW) * GUESS_MAX_POINTS


def compute_trivia_points(trivia_correct: int) -> float:
    return (trivia_correct / TRIVIA_MAX_RAW) * TRIVIA_MAX_POINTS


def compute_maths_points(elapsed_seconds: float | None) -> float:
    """
    None means Maths hasn't been completed yet today — 0 points, same as an
    uncompleted Guess/Trivia's raw score being 0 in the legacy formula.
    """
    if elapsed_seconds is None:
        return 0.0

    first_time, first_points = MATHS_BREAKPOINTS[0]
    if elapsed_seconds <= first_time:
        return float(first_points)

    last_time, last_points = MATHS_BREAKPOINTS[-1]
    if elapsed_seconds >= last_time:
        return float(last_points)

    for (t0, p0), (t1, p1) in zip(MATHS_BREAKPOINTS, MATHS_BREAKPOINTS[1:]):
        if t0 <= elapsed_seconds <= t1:
            fraction = (elapsed_seconds - t0) / (t1 - t0)
            return p0 + fraction * (p1 - p0)

    # Unreachable: MATHS_BREAKPOINTS spans [first_time, last_time] with no gaps, and
    # both ends are handled above.
    raise AssertionError(f"elapsed_seconds={elapsed_seconds!r} not covered by MATHS_BREAKPOINTS")


def compute_total_score(*, guess_score: float, trivia_correct: int, maths_elapsed_seconds: float | None) -> float:
    total = (
        compute_guess_points(guess_score)
        + compute_trivia_points(trivia_correct)
        + compute_maths_points(maths_elapsed_seconds)
    )
    return round(total, 1)


def compute_legacy_total_score(maths_correct: int, trivia_correct: int, guess_score: float) -> float:
    # The original formula: an unweighted raw-count sum out of 35 (20 + 5 + 10).
    # Preserved byte-for-byte for pre-cutover dates — see module docstring.
    return round(maths_correct + trivia_correct + guess_score, 1)


def uses_normalized_scoring(for_date: date) -> bool:
    return for_date >= SCORING_V2_CUTOVER_DATE


def compute_row_total(
    row_date: str,
    *,
    maths_correct: int,
    trivia_correct: int,
    guess_score: float,
    maths_elapsed_seconds: float | None,
) -> float:
    """
    Single entry point complete_game_attempt calls for every insert/update of a
    `scores` row's total_score — picks legacy vs. normalized purely from row_date, so
    the formula choice can never drift from what a row's other columns actually mean.
    """
    if uses_normalized_scoring(date.fromisoformat(row_date)):
        return compute_total_score(
            guess_score=guess_score, trivia_correct=trivia_correct, maths_elapsed_seconds=maths_elapsed_seconds
        )
    return compute_legacy_total_score(maths_correct, trivia_correct, guess_score)
