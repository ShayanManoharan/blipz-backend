# games.py
# Handles all game-related API endpoints
# Routes for fetching daily content and submitting scores for all 3 games

from fastapi import APIRouter, HTTPException
from app.agents.content_generator import generate_daily_content
from app.agents.guess_scorer import score_guess
from app.database import supabase
from app.models.schemas import MathsScoreSubmit, GuessScoreSubmit, TriviaScoreSubmit
from datetime import date

router = APIRouter()

def upsert_score(user_id: str, today: str, field: str, value):
    # Check if a score row exists for this user today
    existing = supabase.table("scores").select("*").eq("user_id", user_id).eq("date", today).execute()

    if existing.data:
        # Update existing row
        current = existing.data[0]
        maths = current["maths_score"]
        trivia = current["trivia_score"]
        guess = float(current["guess_score"])

        if field == "maths_score":
            maths = value
        elif field == "trivia_score":
            trivia = value
        elif field == "guess_score":
            guess = value

        total = round(maths + trivia + guess, 1)

        supabase.table("scores").update({
            field: value,
            "total_score": total
        }).eq("user_id", user_id).eq("date", today).execute()
    else:
        # Create new row
        data = {
            "user_id": user_id,
            "date": today,
            "maths_score": 0,
            "trivia_score": 0,
            "guess_score": 0,
            field: value,
            "total_score": round(value, 1)
        }
        supabase.table("scores").insert(data).execute()

@router.get("/test")
def test():
    return {"message": "Games router is working"}

@router.post("/generate-daily-content")
async def trigger_daily_content():
    result = await generate_daily_content()
    return result

@router.get("/daily-content")
def get_daily_content():
    today = date.today().isoformat()
    result = supabase.table("daily_content").select("*").eq("date", today).execute()
    if not result.data:
        return {"message": "No content generated yet for today"}
    return result.data[0]

@router.post("/submit-guess")
async def submit_guess(body: GuessScoreSubmit):
    today = date.today().isoformat()

    # Get today's actual prompt
    content = supabase.table("daily_content").select("image_prompt").eq("date", today).execute()
    if not content.data:
        raise HTTPException(status_code=404, detail="No content found for today")

    actual_prompt = content.data[0]["image_prompt"]

    # Score the guess
    score = await score_guess(body.guess, actual_prompt)

    # Save to database
    upsert_score(body.user_id, today, "guess_score", score)

    return {
        "user_id": body.user_id,
        "guess": body.guess,
        "score": score,
        "date": today
    }

@router.post("/submit-maths")
def submit_maths(body: MathsScoreSubmit):
    today = date.today().isoformat()

    # Save to database
    upsert_score(body.user_id, today, "maths_score", body.score)

    return {
        "user_id": body.user_id,
        "maths_score": body.score,
        "date": today
    }

@router.post("/submit-trivia")
def submit_trivia(body: TriviaScoreSubmit):
    today = date.today().isoformat()

    # Get today's trivia questions to check answers
    content = supabase.table("daily_content").select("trivia_questions").eq("date", today).execute()
    if not content.data:
        raise HTTPException(status_code=404, detail="No content found for today")

    questions = content.data[0]["trivia_questions"]

    # Score the answers
    correct = 0
    for i, question in enumerate(questions):
        if i < len(body.answers) and body.answers[i] == question["answer"]:
            correct += 1

    # Save to database
    upsert_score(body.user_id, today, "trivia_score", correct)

    return {
        "user_id": body.user_id,
        "trivia_score": correct,
        "correct": correct,
        "total": len(questions),
        "date": today
    }