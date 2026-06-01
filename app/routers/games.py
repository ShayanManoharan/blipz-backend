# games.py
# Handles all game-related API endpoints
# Routes for fetching daily content and submitting scores

from fastapi import APIRouter
from app.agents.content_generator import generate_daily_content
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