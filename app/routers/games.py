# games.py
# Handles all game-related API endpoints
# Routes for fetching daily content and submitting scores

from fastapi import APIRouter, HTTPException
from app.agents.content_generator import generate_daily_content
from app.agents.guess_scorer import score_guess
from app.database import supabase
from app.models.schemas import MathsScoreSubmit, GuessScoreSubmit, TriviaScoreSubmit
from datetime import date

router = APIRouter()

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

    return {
        "user_id": body.user_id,
        "guess": body.guess,
        "score": score,
        "date": today
    }