# content_generator.py
# Daily Content Generator Agent
# Generates each day's content (image, trivia, math) ahead of when it's needed and
# publishes it at the UTC day boundary — see PRODUCTION_AUDIT.md's deployment plan.
# Uses OpenAI for both image generation and text, and Supabase for storage/persistence.
#
# Pipeline (deliberately two separate, both-idempotent steps):
#   generate_content_for_date(content_date) — produces a fully validated package and
#     stores it with status='ready'. Safe to call more than once for the same date:
#     a 'ready' or 'published' row short-circuits without spending any OpenAI calls.
#   publish_content_for_date(content_date) — flips a 'ready' row to 'published' (what
#     GET /games/daily-content is actually allowed to serve). If nothing is 'ready'
#     yet, activates a fallback package instead of leaving the day with no content.
# Nothing is ever written to daily_content until the ENTIRE package (image generated,
# uploaded, upload verified, all 5 trivia questions validated, math problems built) is
# complete in memory — a failure partway through raises before any row is
# inserted/updated, so there is no partially-generated daily package.

import base64
import json
import logging
import random
import re
from datetime import date, timedelta

import httpx
from openai import OpenAI

from app.config import settings
from app.database import supabase
from app.time_utils import utc_now, utc_today, utc_tomorrow

openai_client = OpenAI(api_key=settings.openai_api_key)
logger = logging.getLogger("blipz.content_generator")


class ContentGenerationError(Exception):
    """A complete, valid daily content package could not be produced or verified."""

def parse_trivia_questions(trivia_text):
    trivia_text = trivia_text.strip()
    if trivia_text.startswith("```"):
        trivia_text = trivia_text.split("\n", 1)[1].rsplit("```", 1)[0]

    try:
        return json.loads(trivia_text)
    except json.JSONDecodeError:
        # Model sometimes adds stray prose around the array despite instructions —
        # fall back to extracting the outermost [...] before giving up.
        match = re.search(r"\[.*\]", trivia_text, re.DOTALL)
        if not match:
            raise ValueError(f"Could not parse trivia questions from model output: {trivia_text!r}")
        return json.loads(match.group())


TRIVIA_OPTION_IDS = ("A", "B", "C", "D")
EXPECTED_TRIVIA_QUESTION_COUNT = 5


def normalize_trivia_question(raw: dict, index: int) -> dict:
    """
    Validates and normalizes one raw model-generated trivia question into the
    canonical stored shape: {id, question, category, options, correct_option_id}.

    Raises ValueError on any malformed field — see PRODUCTION_AUDIT.md's Trivia
    grading fix: this used to be an unconstrained free-text `answer` field trusted
    without validation, which silently tolerated shapes that made grading
    impossible to get right (see games.py's id-based grading, which depends on
    every question actually having exactly 4 uniquely-texted options and a
    correct_option_id that is genuinely one of them).
    """
    question = str(raw.get("question", "")).strip()
    if not question:
        raise ValueError(f"Trivia question {index} is missing non-empty 'question' text")

    category = str(raw.get("category", "")).strip()
    if not category:
        raise ValueError(f"Trivia question {index} is missing non-empty 'category'")

    options = raw.get("options")
    if not isinstance(options, list) or len(options) != 4:
        raise ValueError(f"Trivia question {index} must have exactly 4 options, got {options!r}")

    options = [str(opt).strip() for opt in options]
    if any(not opt for opt in options):
        raise ValueError(f"Trivia question {index} has an empty option: {options!r}")
    if len({opt.casefold() for opt in options}) != len(options):
        raise ValueError(f"Trivia question {index} has duplicate options: {options!r}")

    answer = str(raw.get("answer", "")).strip().upper()
    if answer not in TRIVIA_OPTION_IDS:
        raise ValueError(f"Trivia question {index} has an invalid answer/correct_option_id: {raw.get('answer')!r}")

    return {
        "id": f"q{index}",
        "question": question,
        "category": category,
        "options": options,
        "correct_option_id": answer,
    }


def normalize_trivia_questions(raw_questions) -> list[dict]:
    if not isinstance(raw_questions, list) or len(raw_questions) != EXPECTED_TRIVIA_QUESTION_COUNT:
        raise ValueError(
            f"Expected exactly {EXPECTED_TRIVIA_QUESTION_COUNT} trivia questions, got {raw_questions!r}"
        )
    return [normalize_trivia_question(raw, i) for i, raw in enumerate(raw_questions)]

MATH_OPERATIONS = ("add", "subtract", "multiply", "divide")


def compute_math_answer(left_operand, right_operand, operation):
    if operation == "add":
        return left_operand + right_operand
    if operation == "subtract":
        return left_operand - right_operand
    if operation == "multiply":
        return left_operand * right_operand
    if operation == "divide":
        return left_operand // right_operand
    raise ValueError(f"Unknown math operation: {operation!r}")


def generate_math_problems(count=20):
    # Stores operands + operation rather than a rendered question string plus a
    # separate answer key — the client (and compute_math_answer above, server-side)
    # both derive the answer from the same two numbers instead of one side trusting a
    # precomputed value that could drift. See PRODUCTION_AUDIT.md B1 follow-up.
    problems = []
    for _ in range(count):
        operation = random.choice(MATH_OPERATIONS)

        if operation in ("add", "subtract"):
            left, right = random.randint(2, 100), random.randint(2, 100)
        elif operation == "multiply":
            left, right = random.randint(2, 12), random.randint(2, 100)
        else:  # divide — keep it evenly divisible, same as before
            right = random.randint(2, 12)
            quotient = random.randint(2, 100)
            left = quotient * right

        problems.append({
            "left_operand": left,
            "right_operand": right,
            "operation": operation,
        })
    return problems

def _generate_image(content_date: date) -> tuple[str, str]:
    logger.info("Image prompt generation started (content_date=%s)", content_date)
    prompt_response = openai_client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[{
            "role": "user",
            "content": "Generate a single funny, creative, and specific image prompt for an AI image "
                       "generation game. It should be vivid and descriptive but not too long. "
                       "Just return the prompt text, nothing else. "
                       "Example: 'An astronaut eating ramen on the moon watching the sunset'"
        }]
    )
    image_prompt = prompt_response.choices[0].message.content.strip()
    if not image_prompt:
        raise ContentGenerationError("Image prompt generation returned empty text")

    # Never log the prompt itself at INFO — it's the literal Guess answer.
    logger.info("Image generation started (content_date=%s)", content_date)
    image_response = openai_client.images.generate(
        model="gpt-image-1",
        prompt=image_prompt,
        size="1024x1024",
        quality="auto",
        n=1,
    )

    # gpt-image-1 returns base64 directly, not a URL
    image_data = base64.b64decode(image_response.data[0].b64_json)
    if not image_data:
        raise ContentGenerationError("Image generation returned empty image data")

    file_name = f"daily/{content_date.isoformat()}.png"

    try:
        supabase.storage.from_("blipz-images").remove([file_name])
    except Exception:
        pass  # nothing to remove — fine, this is best-effort cleanup of a prior attempt

    logger.info("Storage upload started (content_date=%s, file=%s)", content_date, file_name)
    supabase.storage.from_("blipz-images").upload(file_name, image_data, {"content-type": "image/png"})
    image_url = supabase.storage.from_("blipz-images").get_public_url(file_name)
    if not image_url:
        raise ContentGenerationError("Storage did not return a public URL after upload")

    # Confirm the upload is actually publicly fetchable before this content can ever be
    # considered publishable — catches a storage failure the SDK call itself didn't
    # raise on (e.g. bucket policy issue), rather than discovering it when a player's
    # AsyncImage fails to load hours later.
    try:
        head_response = httpx.head(image_url, timeout=10.0)
        if head_response.status_code >= 400:
            raise ContentGenerationError(
                f"Uploaded image URL returned status {head_response.status_code}, not publishable"
            )
    except httpx.HTTPError as e:
        raise ContentGenerationError(f"Could not verify uploaded image URL is reachable: {e}")

    logger.info("Storage upload verified (content_date=%s)", content_date)
    return image_prompt, image_url


def _generate_trivia(content_date: date) -> list[dict]:
    # Retried once on malformed output (bad option count, duplicate options, non-A-D
    # answer, etc.) since the model occasionally drifts from the requested shape —
    # see normalize_trivia_questions.
    trivia_prompt = {
        "role": "user",
        "content": "Generate exactly 5 trivia questions, one from each of these categories: "
                   "1) Gen Z pop culture, 2) Millennial nostalgia (2000s-2010s), 3) Sports, "
                   "4) Science or History, 5) Wild card (any topic). Each question must have "
                   "exactly 4 non-empty, unique answer options. "
                   "Return ONLY a JSON array in this exact format with no extra text:\n"
                   '[{"question": "...", "category": "...", "options": ["...", "...", "...", "..."], "answer": "A"}]'
    }
    trivia_questions = None
    last_error = None
    for attempt in range(2):
        logger.info("Trivia generation started (content_date=%s, attempt=%d)", content_date, attempt + 1)
        trivia_response = openai_client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[trivia_prompt],
        )
        try:
            raw_questions = parse_trivia_questions(trivia_response.choices[0].message.content)
            trivia_questions = normalize_trivia_questions(raw_questions)
            break
        except ValueError as e:
            last_error = e
            logger.warning("Trivia validation failed (content_date=%s, attempt=%d): %s", content_date, attempt + 1, e)
    if trivia_questions is None:
        raise ContentGenerationError(f"Trivia generation produced malformed output after retry: {last_error}")

    logger.info("Trivia generated and validated (content_date=%s)", content_date)
    return trivia_questions


def generate_content_package(content_date: date) -> dict:
    """
    Produces one complete, validated daily content package in memory. Never touches
    daily_content — raises ContentGenerationError (or lets the underlying OpenAI/
    storage exception propagate) if ANY part fails, so the caller never has a partial
    package to consider storing.
    """
    image_prompt, image_url = _generate_image(content_date)
    trivia_questions = _generate_trivia(content_date)
    math_problems = generate_math_problems(20)
    return {
        "image_prompt": image_prompt,
        "image_url": image_url,
        "trivia_questions": trivia_questions,
        "math_problems": math_problems,
    }


def _log_generation_attempt(content_date: date, operation: str, status: str, *, used_fallback=False, error_message=None):
    try:
        supabase.table("daily_content_generation_log").insert({
            "content_date": content_date.isoformat(),
            "operation": operation,
            "status": status,
            "used_fallback": used_fallback,
            "error_message": error_message,
        }).execute()
    except Exception:
        # Observability logging must never itself take down the generation/publish
        # flow — worst case we lose one audit-trail row, logged here instead.
        logger.exception(
            "Failed to write daily_content_generation_log (content_date=%s, operation=%s)", content_date, operation
        )


def generate_content_for_date(content_date: date | None = None) -> dict:
    """
    Idempotent: if content_date already has a 'ready' or 'published' row, returns
    immediately without spending any OpenAI calls or touching the database again.
    Defaults to tomorrow (UTC) — the whole point of separating generate from publish
    is to produce content before it's needed, not at the moment it's needed.
    """
    content_date = content_date or utc_tomorrow()
    date_str = content_date.isoformat()

    existing = supabase.table("daily_content").select("status").eq("date", date_str).execute()
    if existing.data and existing.data[0]["status"] in ("ready", "published"):
        status = existing.data[0]["status"]
        logger.info("Generation skipped — content_date=%s already %s", content_date, status)
        return {"message": f"Content already {status}", "date": date_str, "status": status}

    logger.info("Daily content generation started (content_date=%s)", content_date)
    try:
        package = generate_content_package(content_date)
    except Exception as e:
        logger.exception("Daily content generation failed (content_date=%s)", content_date)
        _log_generation_attempt(content_date, "generate", "failed", error_message=str(e))
        raise

    now = utc_now().isoformat()
    supabase.table("daily_content").upsert(
        {
            "date": date_str,
            "image_url": package["image_url"],
            "image_prompt": package["image_prompt"],
            "trivia_questions": package["trivia_questions"],
            "math_problems": package["math_problems"],
            "status": "ready",
            "generated_at": now,
            "is_fallback": False,
            "fallback_source_id": None,
        },
        on_conflict="date",
    ).execute()

    _log_generation_attempt(content_date, "generate", "success")
    logger.info("Daily content generation completed (content_date=%s, status=ready)", content_date)
    return {"message": "Daily content generated successfully", "date": date_str, "status": "ready"}


def activate_fallback_for_date(content_date: date) -> dict:
    """
    Publishes a prevalidated emergency package for content_date instead of leaving it
    with no content. Picks the least-recently-used active fallback, explicitly
    avoiding whatever fallback was used the day before when another active option
    exists.
    """
    yesterday = (content_date - timedelta(days=1)).isoformat()
    yesterday_row = supabase.table("daily_content").select("fallback_source_id").eq("date", yesterday).execute()
    excluded_id = (
        yesterday_row.data[0].get("fallback_source_id")
        if yesterday_row.data and yesterday_row.data[0].get("is_fallback")
        else None
    )

    pool = supabase.table("fallback_daily_content").select("*").eq("active", True).execute().data
    if not pool:
        _log_generation_attempt(
            content_date, "publish", "failed", used_fallback=False, error_message="No fallback content available"
        )
        logger.error("No fallback content available to activate (content_date=%s)", content_date)
        raise ContentGenerationError("No ready content and no fallback content available")

    candidates = [f for f in pool if f["id"] != excluded_id] or pool
    candidates.sort(key=lambda f: (f["last_used_date"] or "0000-00-00", f["times_used"]))
    chosen = candidates[0]

    date_str = content_date.isoformat()
    now = utc_now().isoformat()
    supabase.table("daily_content").upsert(
        {
            "date": date_str,
            "image_url": chosen["image_url"],
            "image_prompt": chosen["image_prompt"],
            "trivia_questions": chosen["trivia_questions"],
            "math_problems": chosen["math_problems"],
            "status": "published",
            "generated_at": now,
            "published_at": now,
            "is_fallback": True,
            "fallback_source_id": chosen["id"],
        },
        on_conflict="date",
    ).execute()

    supabase.table("fallback_daily_content").update(
        {"last_used_date": date_str, "times_used": chosen["times_used"] + 1}
    ).eq("id", chosen["id"]).execute()

    _log_generation_attempt(content_date, "publish", "success", used_fallback=True)
    logger.warning(
        "Fallback content activated (content_date=%s, fallback_id=%s, label=%s)",
        content_date, chosen["id"], chosen["label"],
    )
    return {
        "message": "Published via fallback",
        "date": date_str,
        "status": "published",
        "used_fallback": True,
        "fallback_label": chosen["label"],
    }


def publish_content_for_date(content_date: date | None = None) -> dict:
    """
    Idempotent: publishing an already-published date is a no-op success. Flips a
    'ready' row to 'published' via a conditional update (guards the same race a
    concurrent publisher could hit). If nothing is ready yet, activates a fallback
    instead of leaving GET /games/daily-content 404-ing for real players.
    """
    content_date = content_date or utc_today()
    date_str = content_date.isoformat()

    existing = supabase.table("daily_content").select("id, status").eq("date", date_str).execute()

    if existing.data and existing.data[0]["status"] == "published":
        return {"message": "Already published", "date": date_str, "status": "published", "used_fallback": False}

    if existing.data and existing.data[0]["status"] == "ready":
        now = utc_now().isoformat()
        result = (
            supabase.table("daily_content")
            .update({"status": "published", "published_at": now})
            .eq("id", existing.data[0]["id"])
            .eq("status", "ready")
            .execute()
        )
        if result.data:
            _log_generation_attempt(content_date, "publish", "success")
            logger.info("Daily content published (content_date=%s)", content_date)
            return {"message": "Published", "date": date_str, "status": "published", "used_fallback": False}
        # Lost the race — another request published it in between our read and write.
        return {"message": "Already published", "date": date_str, "status": "published", "used_fallback": False}

    logger.warning("No ready content for content_date=%s at publish time — activating fallback", content_date)
    return activate_fallback_for_date(content_date)


# Hand-authored, no OpenAI involved — seeding the emergency pool must never itself
# depend on the thing it exists to be a fallback for. `image_url` is supplied by the
# caller (e.g. a static asset already uploaded to Supabase storage).
_FALLBACK_TRIVIA_PACKAGES = [
    {
        "label": "classic-1",
        "image_prompt": "A sleepy golden retriever wearing a tiny party hat, sitting at a picnic "
                         "table with a single slice of watermelon",
        "trivia_questions": [
            {"question": "What is the largest ocean on Earth?", "category": "Geography",
             "options": ["Atlantic", "Indian", "Pacific", "Arctic"], "answer": "C"},
            {"question": "How many continents are there?", "category": "Geography",
             "options": ["5", "6", "7", "8"], "answer": "C"},
            {"question": "What planet is known as the Red Planet?", "category": "Science",
             "options": ["Venus", "Mars", "Jupiter", "Saturn"], "answer": "B"},
            {"question": "In basketball, how many players per team are on the court at once?", "category": "Sports",
             "options": ["4", "5", "6", "7"], "answer": "B"},
            {"question": "What is the chemical symbol for gold?", "category": "Science",
             "options": ["Ag", "Fe", "Au", "Pb"], "answer": "C"},
        ],
    },
    {
        "label": "classic-2",
        "image_prompt": "A raccoon in a tiny chef's hat flipping a single pancake in a "
                         "moonlit backyard kitchen",
        "trivia_questions": [
            {"question": "What is the capital of Australia?", "category": "Geography",
             "options": ["Sydney", "Melbourne", "Canberra", "Perth"], "answer": "C"},
            {"question": "How many strings does a standard guitar have?", "category": "Music",
             "options": ["4", "5", "6", "7"], "answer": "C"},
            {"question": "What gas do plants primarily absorb from the atmosphere?", "category": "Science",
             "options": ["Oxygen", "Nitrogen", "Carbon dioxide", "Hydrogen"], "answer": "C"},
            {"question": "In soccer, how many players are on the field per team (excluding substitutes)?",
             "category": "Sports", "options": ["9", "10", "11", "12"], "answer": "C"},
            {"question": "What is the smallest prime number?", "category": "Science",
             "options": ["0", "1", "2", "3"], "answer": "C"},
        ],
    },
]


def seed_fallback_content(placeholder_image_url: str) -> dict:
    """
    Inserts the starter emergency pool if it isn't already present (matched by
    `label`, so this is safe to call more than once). Never calls OpenAI — trivia is
    hand-authored above and math problems are generated locally (deterministic
    generation logic, not a model call), so seeding never incurs image-generation
    cost. Replacing `placeholder_image_url` with a real generated image later is a
    separate, explicit, approved action.
    """
    inserted = []
    for package in _FALLBACK_TRIVIA_PACKAGES:
        existing = supabase.table("fallback_daily_content").select("id").eq("label", package["label"]).execute()
        if existing.data:
            continue
        normalized_trivia = normalize_trivia_questions(package["trivia_questions"])
        supabase.table("fallback_daily_content").insert({
            "label": package["label"],
            "image_url": placeholder_image_url,
            "image_prompt": package["image_prompt"],
            "trivia_questions": normalized_trivia,
            "math_problems": generate_math_problems(20),
        }).execute()
        inserted.append(package["label"])
    return {"inserted": inserted, "already_present": [p["label"] for p in _FALLBACK_TRIVIA_PACKAGES if p["label"] not in inserted]}


async def generate_daily_content():
    """
    Back-compat entry point (used by the local-dev-only in-process scheduler and the
    original manual-trigger endpoint): generates AND immediately publishes today's
    (UTC) content in one call, matching the historical "make today's content
    available right now" behavior. Staging/production use generate_content_for_date
    and publish_content_for_date separately instead — see app/routers/admin_content.py.
    """
    today = utc_today()
    generate_result = generate_content_for_date(today)
    publish_result = publish_content_for_date(today)
    return {**generate_result, **publish_result}