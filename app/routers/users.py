# users.py
# Handles fetching the current authenticated user's profile

from fastapi import APIRouter, Depends
from app.auth import get_current_user_id
from app.database import supabase

router = APIRouter()

@router.get("/me")
def get_me(user_id: str = Depends(get_current_user_id)):
    result = supabase.table("users").select("id, username, current_streak, longest_streak").eq("id", user_id).execute()
    if not result.data:
        return {"id": user_id, "username": None, "current_streak": 0, "longest_streak": 0}
    return result.data[0]
