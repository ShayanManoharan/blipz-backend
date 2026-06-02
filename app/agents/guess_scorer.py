# guess_scorer.py
# Guess Scorer Agent
# Takes a player's text description of the daily image
# Compares it against the hidden prompt and scores it out of 10
# Uses GPT-4o-mini to reason about semantic similarity

import os
from openai import OpenAI
from dotenv import load_dotenv

load_dotenv()

openai_client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

async def score_guess(player_guess: str, actual_prompt: str) -> float:
    response = openai_client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[{
            "role": "system",
            "content": (
                "You are a scoring judge for an image guessing game. "
                "A player is shown an AI-generated image and must describe what they see. "
                "You compare their description to the actual prompt used to generate the image. "
                "Score their guess from 0.0 to 10.0 based on these criteria:\n"
                "- 9.0-10.0: Almost exact match, captured the key subjects, actions, and setting\n"
                "- 7.0-8.9: Got the main subjects and general scene correct\n"
                "- 5.0-6.9: Got some elements right but missed key details\n"
                "- 3.0-4.9: Vaguely related, got one element correct\n"
                "- 0.0-2.9: Completely wrong or unrelated\n"
                "Be generous with synonyms and partial credit. "
                "Return ONLY a single decimal number like 7.5 — nothing else."
            )
        }, {
            "role": "user",
            "content": f"Actual prompt: {actual_prompt}\nPlayer guess: {player_guess}"
        }]
    )

    score_text = response.choices[0].message.content.strip()
    score = round(float(score_text), 1)
    return max(0.0, min(10.0, score))





