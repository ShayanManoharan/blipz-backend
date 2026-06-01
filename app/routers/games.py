# games.py
# Handles all game-related API endpoints
# Submit scores for Quick Maths, Daily Trivia, and AI Prompt Guess

from fastapi import APIRouter

router = APIRouter()

@router.get("/test")
def test():
    return {"message": "Games router is working"}