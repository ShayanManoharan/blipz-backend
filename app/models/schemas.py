# schemas.py
# Defines the shape of all data coming in and out of the API
# Pydantic models validate requests and responses automatically

from pydantic import BaseModel
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

class GuessScoreSubmit(BaseModel):
    guess: str

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
    question: str
    answer: int
    # NOTE: kept, unlike Trivia/Guess below. Quick Maths' "type the correct number to
    # auto-advance" mechanic checks answers on-device with no network round trip per
    # keystroke — that's core to the speed-run feel and out of scope to redesign here.
    # This is a documented, accepted residual risk: a client could read this and submit
    # a perfect score without playing. /submit-maths still grades server-side
    # independently either way, so this doesn't affect leaderboard trust beyond that
    # one known gap. See PRODUCTION_AUDIT.md.


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


# --- Leaderboard ---
class LeaderboardEntry(BaseModel):
    username: str
    total_score: float
    date: date

# --- Friends ---
class AddFriendRequest(BaseModel):
    friend_username: str