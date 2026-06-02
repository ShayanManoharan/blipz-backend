# leaderboard.py
# Handles all leaderboard-related API endpoints
# Global daily leaderboard, friends leaderboard, and daily average score

from fastapi import APIRouter
from app.database import supabase
from datetime import date
import random

router = APIRouter()

# Message pools based on average guess score
MESSAGES = {
    "brutal": [
        "brutal day 💀",
        "nobody was ready for that one 😵",
        "the AI cooked today 🔥",
        "respectfully… what was that image 😭",
        "y'all got cooked fr 💀",
        "the AI said choose violence today 😤",
        "not a single soul was prepared 🫠",
        "that was criminal 🚨"
    ],
    "tough": [
        "tuff one today 😮‍💨",
        "not easy out here 😓",
        "the struggle was real 😤",
        "points were hard to come by 📉",
        "AI said good luck 🫡",
        "everyone felt that one 😩",
        "it be like that sometimes 🤷",
        "respect to anyone who scored above 5 💪"
    ],
    "decent": [
        "decent day ✌️",
        "some of y'all got it 👀",
        "fair game today ⚖️",
        "solid effort across the board 👏",
        "middle of the road kind of day 🛣️",
        "not bad not great 😐",
        "average day for average people 😌",
        "could've been worse 🤞"
    ],
    "easy": [
        "easy work 🧠",
        "y'all ate today 🍽️",
        "the crowd was locked in 🎯",
        "too easy? 😏",
        "everybody was on today 🔛",
        "the AI went easy on us 😎",
        "we feasting today 🥳",
        "locked in and locked out 🔐"
    ],
    "free": [
        "free points today 😭",
        "everyone cooked 👨‍🍳",
        "was that even a challenge 😴",
        "GG ez 🏆",
        "the AI went soft today 🧸",
        "even my grandma woulda got this one 👵",
        "points on points on points 💰",
        "that was a gift 🎁"
    ]
}

def get_daily_message(average_score: float) -> str:
    if average_score < 3:
        pool = MESSAGES["brutal"]
    elif average_score < 5:
        pool = MESSAGES["tough"]
    elif average_score < 7:
        pool = MESSAGES["decent"]
    elif average_score < 9:
        pool = MESSAGES["easy"]
    else:
        pool = MESSAGES["free"]
    return random.choice(pool)

@router.get("/test")
def test():
    return {"message": "Leaderboard router is working"}

@router.get("/global")
def get_global_leaderboard():
    today = date.today().isoformat()

    result = supabase.table("scores").select(
        "user_id, total_score, guess_score, maths_score, trivia_score, users(username)"
    ).eq("date", today).order("total_score", desc=True).limit(50).execute()

    if not result.data:
        return {
            "date": today,
            "message": "no scores yet today",
            "average_score": 0,
            "leaderboard": []
        }

    scores = result.data
    average_guess = round(
        sum(s["guess_score"] for s in scores) / len(scores), 1
    )

    leaderboard = []
    for i, s in enumerate(scores):
        leaderboard.append({
            "rank": i + 1,
            "username": s["users"]["username"] if s["users"] else "unknown",
            "total_score": s["total_score"],
            "guess_score": s["guess_score"],
            "maths_score": s["maths_score"],
            "trivia_score": s["trivia_score"]
        })

    return {
        "date": today,
        "message": get_daily_message(average_guess),
        "average_guess_score": average_guess,
        "leaderboard": leaderboard
    }

@router.get("/friends/{user_id}")
def get_friends_leaderboard(user_id: str):
    today = date.today().isoformat()

    # Get friend IDs
    friends = supabase.table("friends").select("friend_id").eq("user_id", user_id).execute()
    friend_ids = [f["friend_id"] for f in friends.data] + [user_id]

    result = supabase.table("scores").select(
        "user_id, total_score, guess_score, maths_score, trivia_score, users(username)"
    ).eq("date", today).in_("user_id", friend_ids).order("total_score", desc=True).execute()

    if not result.data:
        return {
            "date": today,
            "message": "no scores yet today",
            "leaderboard": []
        }

    scores = result.data
    leaderboard = []
    for i, s in enumerate(scores):
        leaderboard.append({
            "rank": i + 1,
            "username": s["users"]["username"] if s["users"] else "unknown",
            "total_score": s["total_score"],
            "guess_score": s["guess_score"],
            "maths_score": s["maths_score"],
            "trivia_score": s["trivia_score"]
        })

    return {
        "date": today,
        "leaderboard": leaderboard
    }