# users.py
# Handles fetching the current authenticated user's profile

from datetime import date

from fastapi import APIRouter, Depends
from app.auth import get_current_user_id
from app.database import supabase

router = APIRouter()

@router.get("/me")
def get_me(user_id: str = Depends(get_current_user_id)):
    result = supabase.table("users").select("id, username, current_streak, longest_streak").eq("id", user_id).execute()
    profile = result.data[0] if result.data else {
        "id": user_id, "username": None, "current_streak": 0, "longest_streak": 0
    }

    today = date.today().isoformat()
    score_result = supabase.table("scores").select(
        "maths_score, trivia_score, guess_score, total_score"
    ).eq("user_id", user_id).eq("date", today).execute()
    today_score = score_result.data[0] if score_result.data else {
        "maths_score": 0, "trivia_score": 0, "guess_score": 0, "total_score": 0
    }

    return {**profile, **today_score}
