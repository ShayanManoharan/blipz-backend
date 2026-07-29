# leaderboard_narrator.py
# Leaderboard Narrator Agent
# Writes a short, funny one-line reaction to how everyone did today,
# based on the average AI Prompt Guess score across all players.

from openai import OpenAI
from app.config import settings

openai_client = OpenAI(api_key=settings.openai_api_key)

async def generate_daily_message(average_guess_score: float) -> str:
    response = openai_client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[{
            "role": "user",
            "content": (
                "You are the funny, slightly unhinged narrator for a daily online guessing "
                "game's leaderboard. Today's average score (out of 10) across all players "
                f"guessing an AI-generated image was {average_guess_score}. Write ONE short, "
                "funny line (under 12 words) reacting to how everyone did today. Use a casual "
                "Gen Z internet tone with 1-2 emojis, like a witty group chat message. "
                "Return ONLY the line, nothing else."
            )
        }]
    )
    return response.choices[0].message.content.strip()
