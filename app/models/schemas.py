# schemas.py
# Defines the shape of all data coming in and out of the API
# Pydantic models validate requests and responses automatically

from pydantic import BaseModel, Field
from datetime import date

# --- User ---
class UserCreate(BaseModel):
    username: str
    email: str

class UserResponse(BaseModel):
    id: str
    username: str
    email: str
    current_streak: int
    longest_streak: int

# --- Scores ---
class MathsScoreSubmit(BaseModel):
    answers: list[int]
    # Client-measured elapsed time for the whole run. Used only for a loose structural
    # plausibility check server-side (rejects negative/zero/near-instant submissions) —
    # this is not an anti-cheat guarantee, just a sanity check. See games.py.
    elapsed_seconds: float

class GuessScoreSubmit(BaseModel):
    # Bounds are generous for a real guess but reject empty/malformed/oversized input
    # before it ever reaches the OpenAI call.
    guess: str = Field(min_length=1, max_length=300)

class TriviaScoreSubmit(BaseModel):
    answers: list[str]

class ScoreResponse(BaseModel):
    user_id: str
    date: date
    maths_score: int
    trivia_score: int
    guess_score: float
    total_score: float

# --- Daily content (public/player-facing) ---
# These are the ONLY shapes GET /games/daily-content is allowed to return. Never
# serialize the raw daily_content DB row directly — it also contains image_prompt
# (the literal Guess answer) and, historically, this endpoint leaked it plus trivia
# answers before players had a chance to play. See PRODUCTION_AUDIT.md finding B1.
class PublicMathProblem(BaseModel):
    left_operand: int
    right_operand: int
    operation: str  # "add" | "subtract" | "multiply" | "divide"
    # No separate `answer` field. The client computes the expected answer locally from
    # left_operand/right_operand/operation (see MathProblem.swift) — the same numbers
    # it needs anyway to render the question, so this avoids transmitting a redundant
    # answer key without changing what's actually derivable from the payload. This is
    # NOT cheat-proof: a script can compute the answer from these operands just as
    # easily as the app does. Real leaderboard integrity for Maths comes from the
    # one-attempt-per-day enforcement in games.py, not from hiding arithmetic.


class PublicTriviaQuestion(BaseModel):
    question: str
    category: str
    options: list[str]
    # Intentionally no `answer` field. /submit-trivia grades server-side by re-fetching
    # the real question set; the client only ever learns aggregate correct/total.


class PublicDailyContentResponse(BaseModel):
    id: str
    date: str
    image_url: str
    math_problems: list[PublicMathProblem]
    trivia_questions: list[PublicTriviaQuestion]
    # Intentionally no `image_prompt` — that's the literal Guess answer.
    # /submit-guess re-fetches it server-side by date; never trust a client-supplied one.


# --- Trivia post-submission review ---
# Only ever returned once trivia_completed is true for that user/day — see
# GET /games/trivia-review. Revealing correct answers here is safe precisely because
# the one-attempt-per-day enforcement means there is no further submission left to
# exploit with this knowledge.
class TriviaReviewQuestion(BaseModel):
    question: str
    options: list[str]
    selected_answer: str | None
    correct_answer: str
    is_correct: bool


class TriviaReviewResponse(BaseModel):
    date: str
    review: list[TriviaReviewQuestion]


# --- Leaderboard ---
class LeaderboardEntry(BaseModel):
    username: str
    total_score: float
    date: date

# --- Friends ---
class AddFriendRequest(BaseModel):
    friend_username: str