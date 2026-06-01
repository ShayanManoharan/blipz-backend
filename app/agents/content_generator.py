# content_generator.py
# Daily Content Generator Agent
# Runs at midnight and generates all daily content for every player
# Uses OpenAI for both image generation (DALL-E) and text (GPT-4o-mini)
# Stores everything in Supabase so all players see the same content

import os
import json
import random
import httpx
from datetime import date
from openai import OpenAI
from app.database import supabase
from dotenv import load_dotenv

load_dotenv()

openai_client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

def generate_math_problems(count=20):
    problems = []
    operations = ['+', '-', '*', '/']
    for _ in range(count):
        op = random.choice(operations)

        if op == '+':
            a, b = random.randint(2, 100), random.randint(2, 100)
            answer = a + b
        elif op == '-':
            a, b = random.randint(2, 100), random.randint(2, 100)
            answer = a - b
        elif op == '*':
            a, b = random.randint(2, 12), random.randint(2, 100)
            answer = a * b
        elif op == '/':
            b = random.randint(2, 12)
            answer = random.randint(2, 100)
            a = answer * b

        problems.append({
            "question": f"{a} {op} {b}",
            "answer": answer
        })
    return problems

async def generate_daily_content():
    today = date.today().isoformat()

    # Check if content already exists for today
    existing = supabase.table("daily_content").select("*").eq("date", today).execute()
    if existing.data:
        return {"message": "Content already exists for today", "date": today}

    # Step 1 — Generate a fun image prompt using GPT-4o-mini
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

    # Step 2 — Generate the image using DALL-E 3
    image_response = openai_client.images.generate(
        model="gpt-image-1",
        prompt=image_prompt,
        size="1024x1024",
        quality="auto",
        n=1
    )

    # gpt-image-1 returns base64 directly, not a URL
    import base64
    image_data = base64.b64decode(image_response.data[0].b64_json)
    file_name = f"daily/{today}.png"

    # Upload to Supabase storage (remove existing file first if it exists)
    try:
        supabase.storage.from_("blipz-images").remove([file_name])
    except:
        pass

    supabase.storage.from_("blipz-images").upload(
        file_name,
        image_data,
        {"content-type": "image/png"}
    )

    image_url = supabase.storage.from_("blipz-images").get_public_url(file_name)

    # Step 3 — Generate 5 trivia questions using GPT-4o-mini
    trivia_response = openai_client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[{
            "role": "user",
            "content": "Generate exactly 5 trivia questions, one from each of these categories: "
                       "1) Gen Z pop culture, 2) Millennial nostalgia (2000s-2010s), 3) Sports, "
                       "4) Science or History, 5) Wild card (any topic). "
                       "Return ONLY a JSON array in this exact format with no extra text:\n"
                       '[{"question": "...", "category": "...", "options": ["A", "B", "C", "D"], "answer": "A"}]'
        }]
    )

    trivia_text = trivia_response.choices[0].message.content.strip()
    if trivia_text.startswith("```"):
        trivia_text = trivia_text.split("\n", 1)[1].rsplit("```", 1)[0]

    trivia_questions = json.loads(trivia_text)

    # Step 4 — Generate math problems
    math_problems = generate_math_problems(20)

    # Step 5 — Store everything in Supabase
    supabase.table("daily_content").insert({
        "date": today,
        "image_url": image_url,
        "image_prompt": image_prompt,
        "trivia_questions": trivia_questions,
        "math_problems": math_problems
    }).execute()

    return {
        "message": "Daily content generated successfully",
        "date": today,
        "image_prompt": image_prompt,
        "image_url": image_url,
        "trivia_questions": trivia_questions,
        "math_problems": math_problems
    }