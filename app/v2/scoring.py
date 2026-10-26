"""V2 points in integer hundredths. Speed never changes awarded points."""

GAME_MAX_UNITS = 2500
ASSISTED_MAX_UNITS = 1875
DAILY_MAX_UNITS = 10000
SCORING_VERSION = "v2-equal25-v1"


def typed_game_points(*, correct: bool, choices_revealed: bool) -> int:
    if type(correct) is not bool or type(choices_revealed) is not bool:
        raise ValueError("Correctness and assistance must be booleans")
    return (ASSISTED_MAX_UNITS if choices_revealed else GAME_MAX_UNITS) if correct else 0


def trivia_points(correct_count: int) -> int:
    if type(correct_count) is not int or not 0 <= correct_count <= 5:
        raise ValueError("Trivia requires an integer correct count from zero through five")
    return correct_count * 500


def percentage_bps(awarded_units: int, max_units: int) -> int:
    if type(awarded_units) is not int or type(max_units) is not int or not 0 <= awarded_units <= max_units or max_units <= 0:
        raise ValueError("Invalid integer point allocation")
    return awarded_units * 10000 // max_units
