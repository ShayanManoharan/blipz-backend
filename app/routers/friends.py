# friends.py
# Handles adding a friend by username and listing your current friends

from fastapi import APIRouter, HTTPException, Depends
from app.auth import get_current_user_id
from app.database import supabase
from app.models.schemas import AddFriendRequest

router = APIRouter()

@router.post("/add")
def add_friend(body: AddFriendRequest, user_id: str = Depends(get_current_user_id)):
    target = supabase.table("users").select("id, username").eq("username", body.friend_username).execute()
    if not target.data:
        raise HTTPException(status_code=404, detail="User not found")

    friend_id = target.data[0]["id"]
    if friend_id == user_id:
        raise HTTPException(status_code=400, detail="Cannot add yourself as a friend")

    existing = supabase.table("friends").select("id").eq("user_id", user_id).eq("friend_id", friend_id).execute()
    if existing.data:
        return {"message": "Already friends", "friend_id": friend_id, "username": target.data[0]["username"]}

    supabase.table("friends").insert({"user_id": user_id, "friend_id": friend_id}).execute()
    return {"message": "Friend added", "friend_id": friend_id, "username": target.data[0]["username"]}

@router.get("/list")
def get_friends(user_id: str = Depends(get_current_user_id)):
    result = supabase.table("friends").select("friend_id, users!friends_friend_id_fkey(username)").eq("user_id", user_id).execute()
    friends = [
        {"id": f["friend_id"], "username": f["users"]["username"] if f["users"] else "unknown"}
        for f in result.data
    ]
    return {"friends": friends}
