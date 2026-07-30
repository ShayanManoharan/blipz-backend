# games.py
# Handles all game-related API endpoints
# Routes for fetching daily content and submitting scores for all 3 games
#
# Completion model (see PRODUCTION_AUDIT.md B2 and its follow-up fix): each of
# maths_completed/guess_completed/trivia_completed is set exactly once per user per
# day, by complete_game_attempt() below, and never overwritten after that. A repeated
# submission to any /submit-* route returns the ORIGINAL stored result instead of
# recomputing — this is what makes re-rolling Guess (and re-charging OpenAI for it)
# impossible, and what makes "one ranked attempt per user/game/day" a real guarantee
# instead of a client-side convention.

from datetime import date, timedelta

from fastapi import APIRouter, HTTPException, Depends, Request
from postgrest.exceptions import APIError

from app.agents.content_generator import generate_daily_content, compute_math_answer
from app.agents.guess_scorer import score_guess
from app.auth import require_admin_token, get_current_user_id
from app.database import supabase
from app.rate_limit import limiter
from app.models.schemas import (
    MathsScoreSubmit, GuessScoreSubmit, TriviaScoreSubmit,
    PublicDailyContentResponse, PublicMathProblem, PublicTriviaQuestion,
    TriviaReviewResponse, TriviaReviewQuestion,
)

router = APIRouter()

POSTGRES_UNIQUE_VIOLATION = "23505"

COMPLETED_FIELD = {"maths": "maths_completed", "guess": "guess_completed", "trivia": "trivia_completed"}
SCORE_FIELD = {"maths": "maths_score", "guess": "guess_score", "trivia": "trivia_score"}

# Very loose sanity floor, not an anti-cheat measure — see PublicMathProblem's own
# docstring-equivalent comment in schemas.py. This only catches degenerate/zero/
# scripted-instant submissions, not a determined attacker who sleeps an appropriate
# duration before calling the API directly.
MIN_PLAUSIBLE_MATHS_SECONDS = 1.0


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


def get_today_score_row(user_id: str, today: str):
    result = supabase.table("scores").select("*").eq("user_id", user_id).eq("date", today).execute()
    return result.data[0] if result.data else None


def complete_game_attempt(user_id: str, today: str, game: str, score, extra_fields: dict | None = None):
    """
    Atomically marks `game` as completed for user_id/today with `score`.

    Returns (row, already_completed_before_this_call). If the game was already
    completed, the row returned is the ORIGINAL stored result — this function never
    overwrites a prior completion, regardless of what `score` is passed this time.

    Concurrency: relies on the existing UNIQUE(user_id, date) constraint on `scores`
    for the insert race, and an atomic conditional UPDATE (`.eq(completed_field, False)`)
    for the same-game double-submit race — both are single PostgREST/Postgres
    statements, so Postgres's own row-level locking serializes concurrent callers
    correctly without any extra application-level locking.
    """
    completed_field = COMPLETED_FIELD[game]
    score_field = SCORE_FIELD[game]
    extra_fields = extra_fields or {}

    existing = get_today_score_row(user_id, today)

    if existing is None:
        maths = score if game == "maths" else 0
        trivia = score if game == "trivia" else 0
        guess = score if game == "guess" else 0
        total = round(maths + trivia + guess, 1)

        insert_data = {
            "user_id": user_id,
            "date": today,
            "maths_score": maths,
            "trivia_score": trivia,
            "guess_score": guess,
            "total_score": total,
            completed_field: True,
            **extra_fields,
        }
        try:
            supabase.table("scores").insert(insert_data).execute()
            update_streak(user_id, today)
            return get_today_score_row(user_id, today), False
        except APIError as e:
            if e.code != POSTGRES_UNIQUE_VIOLATION:
                raise
            # Lost the insert race — another request (for this or a different game)
            # created today's row first. Fall through to the update path below.
            existing = get_today_score_row(user_id, today)

    if existing.get(completed_field):
        return existing, True

    new_maths = score if game == "maths" else existing["maths_score"]
    new_trivia = score if game == "trivia" else existing["trivia_score"]
    new_guess = score if game == "guess" else float(existing["guess_score"])
    total = round(new_maths + new_trivia + new_guess, 1)

    update_data = {score_field: score, completed_field: True, "total_score": total, **extra_fields}

    result = (
        supabase.table("scores")
        .update(update_data)
        .eq("user_id", user_id)
        .eq("date", today)
        .eq(completed_field, False)  # atomic compare-and-swap guard
        .execute()
    )

    if not result.data:
        # Lost the update race — someone else completed this exact game for this row
        # between our read and our write. Their result is authoritative, not ours.
        return get_today_score_row(user_id, today), True

    return result.data[0], False


@router.get("/test")
def test():
    return {"message": "Games router is working"}


@router.post("/generate-daily-content", dependencies=[Depends(require_admin_token)])
async def trigger_daily_content():
    result = await generate_daily_content()
    return result


@router.get("/daily-content", response_model=PublicDailyContentResponse)
def get_daily_content(user_id: str = Depends(get_current_user_id)):
    today = date.today().isoformat()
    result = supabase.table("daily_content").select("*").eq("date", today).execute()
    if not result.data:
        raise HTTPException(status_code=404, detail="No content generated yet for today")

    row = result.data[0]
    return PublicDailyContentResponse(
        id=row["id"],
        date=row["date"],
        image_url=row["image_url"],
        math_problems=[
            PublicMathProblem(
                left_operand=p["left_operand"], right_operand=p["right_operand"], operation=p["operation"]
            )
            for p in row["math_problems"]
        ],
        trivia_questions=[
            PublicTriviaQuestion(question=q["question"], category=q["category"], options=q["options"])
            for q in row["trivia_questions"]
        ],
    )


@router.post("/submit-guess")
@limiter.limit("10/minute")
async def submit_guess(request: Request, body: GuessScoreSubmit, user_id: str = Depends(get_current_user_id)):
    today = date.today().isoformat()

    # Check for an existing completed Guess before ever calling OpenAI — this is the
    # primary defense against re-rolling and repeated charges. (The atomic update
    # inside complete_game_attempt is the backstop for the narrow true-concurrency
    # case where two requests both pass this check before either has written — see
    # PRODUCTION_AUDIT.md follow-up notes on residual risk.)
    existing = get_today_score_row(user_id, today)
    if existing and existing.get("guess_completed"):
        return {
            "user_id": user_id,
            "guess": existing.get("guess_text") or "",
            "score": existing["guess_score"],
            "date": today,
            "already_completed": True,
        }

    content = supabase.table("daily_content").select("image_prompt").eq("date", today).execute()
    if not content.data:
        raise HTTPException(status_code=404, detail="No content found for today")

    # Always the server's own copy — never a client-supplied prompt/rubric/score.
    actual_prompt = content.data[0]["image_prompt"]

    score = await score_guess(body.guess, actual_prompt)

    row, already_completed = complete_game_attempt(
        user_id, today, "guess", score, extra_fields={"guess_text": body.guess}
    )

    return {
        "user_id": user_id,
        "guess": row.get("guess_text") or body.guess,
        "score": row["guess_score"],
        "date": today,
        "already_completed": already_completed,
    }


@router.post("/submit-maths")
def submit_maths(body: MathsScoreSubmit, user_id: str = Depends(get_current_user_id)):
    today = date.today().isoformat()

    content = supabase.table("daily_content").select("math_problems").eq("date", today).execute()
    if not content.data:
        raise HTTPException(status_code=404, detail="No content found for today")

    problems = content.data[0]["math_problems"]

    if len(body.answers) != len(problems):
        raise HTTPException(status_code=400, detail="Answers do not match today's problem set")

    if body.elapsed_seconds < MIN_PLAUSIBLE_MATHS_SECONDS:
        raise HTTPException(status_code=400, detail="Submission is not structurally plausible")

    correct = sum(
        1
        for problem, answer in zip(problems, body.answers)
        if answer == compute_math_answer(problem["left_operand"], problem["right_operand"], problem["operation"])
    )

    row, already_completed = complete_game_attempt(
        user_id, today, "maths", correct, extra_fields={"maths_elapsed_seconds": body.elapsed_seconds}
    )

    return {
        "user_id": user_id,
        "maths_score": row["maths_score"],
        "correct": row["maths_score"],
        "total": len(problems),
        "date": today,
        "already_completed": already_completed,
    }


@router.post("/submit-trivia")
def submit_trivia(body: TriviaScoreSubmit, user_id: str = Depends(get_current_user_id)):
    today = date.today().isoformat()

    content = supabase.table("daily_content").select("trivia_questions").eq("date", today).execute()
    if not content.data:
        raise HTTPException(status_code=404, detail="No content found for today")

    questions = content.data[0]["trivia_questions"]

    if len(body.answers) != len(questions):
        raise HTTPException(status_code=400, detail="Answers do not match today's question set")

    correct = sum(
        1 for question, answer in zip(questions, body.answers) if answer == question["answer"]
    )

    row, already_completed = complete_game_attempt(
        user_id, today, "trivia", correct, extra_fields={"trivia_answers": body.answers}
    )

    return {
        "user_id": user_id,
        "trivia_score": row["trivia_score"],
        "correct": row["trivia_score"],
        "total": len(questions),
        "date": today,
        "already_completed": already_completed,
    }


@router.get("/trivia-review", response_model=TriviaReviewResponse)
def get_trivia_review(user_id: str = Depends(get_current_user_id)):
    today = date.today().isoformat()

    existing = get_today_score_row(user_id, today)
    if not existing or not existing.get("trivia_completed"):
        raise HTTPException(status_code=404, detail="Trivia not completed yet today")

    content = supabase.table("daily_content").select("trivia_questions").eq("date", today).execute()
    if not content.data:
        raise HTTPException(status_code=404, detail="No content found for today")

    questions = content.data[0]["trivia_questions"]
    user_answers = existing.get("trivia_answers") or []

    review = []
    for i, question in enumerate(questions):
        selected = user_answers[i] if i < len(user_answers) else None
        review.append(
            TriviaReviewQuestion(
                question=question["question"],
                options=question["options"],
                selected_answer=selected,
                correct_answer=question["answer"],
                is_correct=selected == question["answer"],
            )
        )

    return TriviaReviewResponse(date=today, review=review)
