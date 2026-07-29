# games.py
# Handles all game-related API endpoints
# Routes for fetching daily content and submitting scores for all 3 games

from fastapi import APIRouter, HTTPException, Depends
from app.agents.content_generator import generate_daily_content
from app.agents.guess_scorer import score_guess
from app.auth import require_admin_token, get_current_user_id
from app.database import supabase
from app.models.schemas import MathsScoreSubmit, GuessScoreSubmit, TriviaScoreSubmit
from datetime import date, timedelta

router = APIRouter()

def update_streak(user_id: str, today: str):
    user = supabase.table("users").select("current_streak, longest_streak").eq("id", user_id).execute()
    if not user.data:
        return

    current_streak = user.data[0]["current_streak"]
    longest_streak = user.data[0]["longest_streak"]

    yesterday = (date.fromisoformat(today) - timedelta(days=1)).isoformat()
    played_yesterday = supabase.table("scores").select("id").eq("user_id", user_id).eq("date", yesterday).execute()

    new_streak = current_streak + 1 if played_yesterday.data else 1
    new_longest = max(longest_streak, new_streak)

    supabase.table("users").update({
        "current_streak": new_streak,
        "longest_streak": new_longest
    }).eq("id", user_id).execute()

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
        # Create new row - this is the user's first game of the day, so update their streak
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
        update_streak(user_id, today)

@router.get("/test")
def test():
    return {"message": "Games router is working"}

@router.post("/generate-daily-content", dependencies=[Depends(require_admin_token)])
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
async def submit_guess(body: GuessScoreSubmit, user_id: str = Depends(get_current_user_id)):
    today = date.today().isoformat()

    # Get today's actual prompt
    content = supabase.table("daily_content").select("image_prompt").eq("date", today).execute()
    if not content.data:
        raise HTTPException(status_code=404, detail="No content found for today")

    actual_prompt = content.data[0]["image_prompt"]

    # Score the guess
    score = await score_guess(body.guess, actual_prompt)

    # Save to database
    upsert_score(user_id, today, "guess_score", score)

    return {
        "user_id": user_id,
        "guess": body.guess,
        "score": score,
        "date": today
    }

@router.post("/submit-maths")
def submit_maths(body: MathsScoreSubmit, user_id: str = Depends(get_current_user_id)):
    today = date.today().isoformat()

    # Get today's math problems to check answers
    content = supabase.table("daily_content").select("math_problems").eq("date", today).execute()
    if not content.data:
        raise HTTPException(status_code=404, detail="No content found for today")

    problems = content.data[0]["math_problems"]

    # Score the answers
    correct = 0
    for i, problem in enumerate(problems):
        if i < len(body.answers) and body.answers[i] == problem["answer"]:
            correct += 1

    # Save to database
    upsert_score(user_id, today, "maths_score", correct)

    return {
        "user_id": user_id,
        "maths_score": correct,
        "correct": correct,
        "total": len(problems),
        "date": today
    }

@router.post("/submit-trivia")
def submit_trivia(body: TriviaScoreSubmit, user_id: str = Depends(get_current_user_id)):
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
    upsert_score(user_id, today, "trivia_score", correct)

    return {
        "user_id": user_id,
        "trivia_score": correct,
        "correct": correct,
        "total": len(questions),
        "date": today
    }