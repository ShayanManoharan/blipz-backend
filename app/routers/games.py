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

import asyncio
import logging
from datetime import date, datetime, timedelta, timezone

from fastapi import APIRouter, HTTPException, Depends, Request
from fastapi.responses import JSONResponse
from postgrest.exceptions import APIError

from app.agents.content_generator import generate_daily_content, compute_math_answer, TRIVIA_OPTION_IDS
from app.agents.guess_scorer import score_guess
from app.auth import require_admin_token, get_current_user_id
from app.database import supabase
from app.rate_limit import limiter
from app.time_utils import utc_today
from app.models.schemas import (
    MathsScoreSubmit, GuessScoreSubmit, TriviaScoreSubmit,
    PublicDailyContentResponse, PublicMathProblem, PublicTriviaQuestion,
    TriviaReviewResponse, TriviaReviewQuestion, GuessReviewResponse,
)

router = APIRouter()
logger = logging.getLogger("blipz.games")

POSTGRES_UNIQUE_VIOLATION = "23505"

COMPLETED_FIELD = {"maths": "maths_completed", "guess": "guess_completed", "trivia": "trivia_completed"}
SCORE_FIELD = {"maths": "maths_score", "guess": "guess_score", "trivia": "trivia_score"}

# Very loose sanity floor, not an anti-cheat measure — see PublicMathProblem's own
# docstring-equivalent comment in schemas.py. This only catches degenerate/zero/
# scripted-instant submissions, not a determined attacker who sleeps an appropriate
# duration before calling the API directly.
MIN_PLAUSIBLE_MATHS_SECONDS = 1.0

# --- Guess scoring-attempt reservation (see PRODUCTION_AUDIT.md B23 fix) ------------
# Closes the concurrency-cost window left by complete_game_attempt's compare-and-swap:
# that CAS guarantees only one score is ever *persisted*, but does nothing to stop two
# simultaneous first requests from both calling OpenAI before either writes. These
# states let the backend atomically reserve the right to call OpenAI at all, so a
# concurrent request never independently starts its own scoring call.
GUESS_STATUS_NOT_STARTED = "not_started"
GUESS_STATUS_SCORING = "scoring"
GUESS_STATUS_COMPLETED = "completed"
GUESS_STATUS_FAILED = "failed"

# If a reservation has been sitting in "scoring" longer than this, its owner is assumed
# dead (crashed process, dropped connection, etc.) — a later request may reclaim it and
# retry the OpenAI call itself. Comfortably above typical OpenAI latency (seconds) but
# short enough that a real crash self-heals within one gameplay session.
GUESS_SCORING_STALE_AFTER_SECONDS = 30

# Bounded wait for a request that finds scoring already in progress: rather than call
# OpenAI itself or block indefinitely, it briefly re-polls the stored row in case the
# in-flight scoring call (typically a couple seconds) finishes within this window. If it
# doesn't, the caller gets a 202 telling it to retry shortly instead of an error.
GUESS_WAIT_POLL_ATTEMPTS = 4
GUESS_WAIT_POLL_INTERVAL_SECONDS = 0.5


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


def _trivia_question_id(question: dict, index: int) -> str:
    # Falls back to a positional id for daily_content rows generated before the
    # normalize_trivia_questions fix (see content_generator.py) — those rows are never
    # rewritten, so reads stay correct for them without a JSONB backfill migration.
    return question.get("id") or f"q{index}"


def _trivia_correct_option_id(question: dict) -> str | None:
    # `answer` is the pre-fix key (see PRODUCTION_AUDIT.md's Trivia grading fix); still
    # read for old daily_content rows that predate `correct_option_id`.
    return question.get("correct_option_id") or question.get("answer")


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


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _is_stale(started_at_iso: str | None) -> bool:
    if not started_at_iso:
        return True
    started_at = datetime.fromisoformat(started_at_iso)
    age = datetime.now(timezone.utc) - started_at
    return age > timedelta(seconds=GUESS_SCORING_STALE_AFTER_SECONDS)


def _claim_guess_scoring_from_not_started_or_failed(row_id: str):
    result = (
        supabase.table("scores")
        .update({"guess_status": GUESS_STATUS_SCORING, "guess_scoring_started_at": _utcnow_iso()})
        .eq("id", row_id)
        .eq("guess_completed", False)
        .in_("guess_status", [GUESS_STATUS_NOT_STARTED, GUESS_STATUS_FAILED])
        .execute()
    )
    return result.data[0] if result.data else None


def _reclaim_stale_guess_scoring(row_id: str, expected_started_at: str):
    # Optimistic-concurrency guard: only succeeds if guess_scoring_started_at still
    # matches exactly what we just read. If another request reclaimed (or completed) it
    # in the meantime, this affects zero rows and we fall back to "in_progress" rather
    # than double-reclaiming.
    result = (
        supabase.table("scores")
        .update({"guess_status": GUESS_STATUS_SCORING, "guess_scoring_started_at": _utcnow_iso()})
        .eq("id", row_id)
        .eq("guess_completed", False)
        .eq("guess_status", GUESS_STATUS_SCORING)
        .eq("guess_scoring_started_at", expected_started_at)
        .execute()
    )
    return result.data[0] if result.data else None


def _release_guess_reservation_as_failed(row_id: str):
    # Guarded so this only fires if the reservation is still ours (still "scoring") —
    # if a stale-reclaim or completion already moved it on, leave that alone.
    supabase.table("scores").update({"guess_status": GUESS_STATUS_FAILED}).eq("id", row_id).eq(
        "guess_status", GUESS_STATUS_SCORING
    ).execute()


def acquire_guess_scoring_slot(user_id: str, today: str):
    """
    Atomically attempts to claim the right to call OpenAI for today's Guess attempt.

    Returns (outcome, row):
      "acquired"           — caller now owns the reservation and must call OpenAI
                              exactly once, then either complete_game_attempt(...) or
                              _release_guess_reservation_as_failed(row["id"]) on error.
      "already_completed"  — guess_completed is already True; row is the stored result.
      "in_progress"        — another (non-stale) request is actively scoring; caller
                              must not call OpenAI.

    Every state transition here is a single conditional Postgres UPDATE (or an insert
    guarded by the existing UNIQUE(user_id, date) constraint) — never a plain
    check-then-write in application code — so this is safe under multiple concurrent
    requests and multiple backend processes, not just within one process.
    """
    existing = get_today_score_row(user_id, today)

    if existing is None:
        insert_data = {
            "user_id": user_id,
            "date": today,
            "maths_score": 0,
            "trivia_score": 0,
            "guess_score": 0,
            "total_score": 0,
            "guess_status": GUESS_STATUS_SCORING,
            "guess_scoring_started_at": _utcnow_iso(),
        }
        try:
            supabase.table("scores").insert(insert_data).execute()
            return "acquired", get_today_score_row(user_id, today)
        except APIError as e:
            if e.code != POSTGRES_UNIQUE_VIOLATION:
                raise
            # Lost the insert race — another request (for this or a different game)
            # created today's row first. Fall through to the existing-row path below.
            existing = get_today_score_row(user_id, today)

    if existing.get("guess_completed"):
        return "already_completed", existing

    status = existing.get("guess_status") or GUESS_STATUS_NOT_STARTED

    if status in (GUESS_STATUS_NOT_STARTED, GUESS_STATUS_FAILED):
        claimed = _claim_guess_scoring_from_not_started_or_failed(existing["id"])
        if claimed:
            return "acquired", claimed
        # Lost the race — re-check what actually happened.
        existing = get_today_score_row(user_id, today)
        if existing.get("guess_completed"):
            return "already_completed", existing
        status = existing.get("guess_status")

    if status == GUESS_STATUS_SCORING:
        started_at = existing.get("guess_scoring_started_at")
        if _is_stale(started_at):
            claimed = _reclaim_stale_guess_scoring(existing["id"], started_at)
            if claimed:
                return "acquired", claimed
            existing = get_today_score_row(user_id, today)
            if existing.get("guess_completed"):
                return "already_completed", existing
        return "in_progress", existing

    # Unreachable given the CHECK constraint (status is always one of the 4 values),
    # but stay safe rather than ever calling OpenAI from an unrecognized state.
    return "in_progress", existing


@router.get("/test")
def test():
    return {"message": "Games router is working"}


@router.post("/generate-daily-content", dependencies=[Depends(require_admin_token)])
async def trigger_daily_content():
    result = await generate_daily_content()
    return result


@router.get("/daily-content", response_model=PublicDailyContentResponse)
def get_daily_content(user_id: str = Depends(get_current_user_id)):
    today = utc_today().isoformat()
    result = supabase.table("daily_content").select("*").eq("date", today).eq("status", "published").execute()
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
            PublicTriviaQuestion(
                id=_trivia_question_id(q, i), question=q["question"], category=q["category"], options=q["options"]
            )
            for i, q in enumerate(row["trivia_questions"])
        ],
    )


def _todays_actual_prompt(today: str) -> str | None:
    content = supabase.table("daily_content").select("image_prompt").eq("date", today).eq("status", "published").execute()
    if not content.data:
        return None
    return content.data[0]["image_prompt"]


def _guess_completed_response(user_id: str, today: str, row: dict) -> dict:
    return {
        "user_id": user_id,
        "guess": row.get("guess_text") or "",
        "score": row["guess_score"],
        "date": today,
        "already_completed": True,
        # Safe to reveal here — this branch only runs once guess_completed is
        # already true for today, same spoiler-safety rule trivia-review follows.
        "actual_prompt": _todays_actual_prompt(today),
    }


@router.post("/submit-guess")
@limiter.limit("10/minute")
async def submit_guess(request: Request, body: GuessScoreSubmit, user_id: str = Depends(get_current_user_id)):
    today = utc_today().isoformat()

    # Atomically reserves the right to call OpenAI at all — this is the fix for B23's
    # concurrency-cost window. See acquire_guess_scoring_slot's docstring and
    # PRODUCTION_AUDIT.md B23.
    outcome, row = acquire_guess_scoring_slot(user_id, today)

    if outcome == "already_completed":
        return _guess_completed_response(user_id, today, row)

    if outcome == "in_progress":
        # Bounded wait, not an indefinite block: most Guess scoring finishes in a
        # couple of seconds, so briefly re-poll the stored row in case it completes
        # within this window. If it doesn't, tell the caller to retry shortly instead
        # of calling OpenAI ourselves or blocking forever.
        for _ in range(GUESS_WAIT_POLL_ATTEMPTS):
            await asyncio.sleep(GUESS_WAIT_POLL_INTERVAL_SECONDS)
            fresh = get_today_score_row(user_id, today)
            if fresh and fresh.get("guess_completed"):
                return _guess_completed_response(user_id, today, fresh)
        return JSONResponse(
            status_code=202,
            content={
                "status": "scoring_in_progress",
                "detail": "Your Guess is already being scored for today — try again in a few seconds.",
                "date": today,
            },
        )

    # outcome == "acquired" — we now exclusively own the right to call OpenAI for
    # today's Guess attempt. No other request can reach this branch until we either
    # complete it or release it as failed below.
    content = supabase.table("daily_content").select("image_prompt").eq("date", today).eq("status", "published").execute()
    if not content.data:
        _release_guess_reservation_as_failed(row["id"])
        raise HTTPException(status_code=404, detail="No content found for today")

    # Always the server's own copy — never a client-supplied prompt/rubric/score.
    actual_prompt = content.data[0]["image_prompt"]

    try:
        score = await score_guess(body.guess, actual_prompt)
    except Exception as e:
        # Log the exception type/message (diagnostic value) but never the user's
        # guess text or the hidden image prompt.
        logger.warning("Guess scoring failed (user_id=%s): %s", user_id, e)
        # Release the reservation rather than leaving it stuck in "scoring" — the
        # user's daily Guess attempt is not consumed, and a resubmission (naturally
        # throttled by the rate limiter above) will retry cleanly. No internal
        # exception details are exposed to the client.
        _release_guess_reservation_as_failed(row["id"])
        raise HTTPException(
            status_code=502, detail="Guess scoring is temporarily unavailable — please try again."
        )

    row, already_completed = complete_game_attempt(
        user_id,
        today,
        "guess",
        score,
        extra_fields={"guess_text": body.guess, "guess_status": GUESS_STATUS_COMPLETED},
    )

    return {
        "user_id": user_id,
        "guess": row.get("guess_text") or body.guess,
        "score": row["guess_score"],
        "date": today,
        "already_completed": already_completed,
        # Guess is completed as of this response (the write above just succeeded) —
        # safe to reveal now, same rule as trivia-review and _guess_completed_response.
        "actual_prompt": actual_prompt,
    }


@router.post("/submit-maths")
def submit_maths(body: MathsScoreSubmit, user_id: str = Depends(get_current_user_id)):
    today = utc_today().isoformat()

    content = supabase.table("daily_content").select("math_problems").eq("date", today).eq("status", "published").execute()
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
    today = utc_today().isoformat()

    content = supabase.table("daily_content").select("trivia_questions").eq("date", today).eq("status", "published").execute()
    if not content.data:
        raise HTTPException(status_code=404, detail="No content found for today")

    questions = content.data[0]["trivia_questions"]
    expected_ids = [_trivia_question_id(q, i) for i, q in enumerate(questions)]

    submitted_ids = [a.question_id for a in body.answers]
    if len(submitted_ids) != len(expected_ids):
        raise HTTPException(status_code=400, detail="Answers do not match today's question set")
    if len(set(submitted_ids)) != len(submitted_ids):
        raise HTTPException(status_code=400, detail="Duplicate question_id in submission")
    if set(submitted_ids) != set(expected_ids):
        raise HTTPException(status_code=400, detail="Submission contains missing or unknown question_id values")

    # TriviaAnswerSubmit.selected_option_id is already pattern-validated to ^[A-D]$ at
    # the schema layer, so no further "is it a real option id" check is needed here.
    answer_by_question_id = {a.question_id: a.selected_option_id for a in body.answers}

    correct = sum(
        1
        for i, question in enumerate(questions)
        if answer_by_question_id[_trivia_question_id(question, i)] == _trivia_correct_option_id(question)
    )

    stored_answers = [{"question_id": a.question_id, "selected_option_id": a.selected_option_id} for a in body.answers]

    row, already_completed = complete_game_attempt(
        user_id, today, "trivia", correct, extra_fields={"trivia_answers": stored_answers}
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
    today = utc_today().isoformat()

    existing = get_today_score_row(user_id, today)
    if not existing or not existing.get("trivia_completed"):
        raise HTTPException(status_code=404, detail="Trivia not completed yet today")

    content = supabase.table("daily_content").select("trivia_questions").eq("date", today).eq("status", "published").execute()
    if not content.data:
        raise HTTPException(status_code=404, detail="No content found for today")

    questions = content.data[0]["trivia_questions"]
    stored_answers = existing.get("trivia_answers") or []

    # New shape is a list of {"question_id", "selected_option_id"} dicts (see
    # submit_trivia). Rows completed before this fix stored raw list[str] option TEXT
    # instead — those can't be reliably mapped back to a question_id/option_id, so they
    # display as "no selection recorded" rather than guessing. See PRODUCTION_AUDIT.md's
    # Trivia grading fix and the historical-data cleanup notes there.
    selection_by_question_id: dict[str, str] = {}
    if stored_answers and isinstance(stored_answers[0], dict):
        selection_by_question_id = {
            a.get("question_id"): a.get("selected_option_id") for a in stored_answers
        }

    review = []
    for i, question in enumerate(questions):
        qid = _trivia_question_id(question, i)
        options = question["options"]
        correct_option_id = _trivia_correct_option_id(question)
        correct_index = TRIVIA_OPTION_IDS.index(correct_option_id) if correct_option_id in TRIVIA_OPTION_IDS else None
        correct_answer_text = options[correct_index] if correct_index is not None and correct_index < len(options) else "?"

        selected_option_id = selection_by_question_id.get(qid)
        selected_index = TRIVIA_OPTION_IDS.index(selected_option_id) if selected_option_id in TRIVIA_OPTION_IDS else None
        selected_answer_text = options[selected_index] if selected_index is not None and selected_index < len(options) else None

        review.append(
            TriviaReviewQuestion(
                question=question["question"],
                options=options,
                selected_option_id=selected_option_id,
                selected_answer_text=selected_answer_text,
                correct_option_id=correct_option_id or "?",
                correct_answer_text=correct_answer_text,
                is_correct=selected_option_id is not None and selected_option_id == correct_option_id,
            )
        )

    return TriviaReviewResponse(date=today, review=review)


@router.get("/guess-review", response_model=GuessReviewResponse)
def get_guess_review(user_id: str = Depends(get_current_user_id)):
    today = utc_today().isoformat()

    existing = get_today_score_row(user_id, today)
    if not existing or not existing.get("guess_completed"):
        raise HTTPException(status_code=404, detail="Guess not completed yet today")

    actual_prompt = _todays_actual_prompt(today)
    if actual_prompt is None:
        raise HTTPException(status_code=404, detail="No content found for today")

    return GuessReviewResponse(
        date=today,
        guess=existing.get("guess_text") or "",
        score=existing["guess_score"],
        actual_prompt=actual_prompt,
    )
