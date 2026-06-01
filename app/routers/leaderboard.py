# leaderboard.py
# Handles all leaderboard-related API endpoints
# Fetch global and friends leaderboard data

from fastapi import APIRouter

router = APIRouter()

@router.get("/test")
def test():
    return {"message": "Leaderboard router is working"}