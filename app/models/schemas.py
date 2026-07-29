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

# --- Leaderboard ---
class LeaderboardEntry(BaseModel):
    username: str
    total_score: float
    date: date