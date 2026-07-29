# Blipz Implementation Roadmap

Tracks feature status across both repos: `blipz-backend` (FastAPI + Supabase + OpenAI) and
`blipz-ios/Blipz` (SwiftUI). Update the checkboxes as features land — this is the single
source of truth for "what's next," so keep it current rather than trusting memory/plan files.

Legend: `[x]` done and verified · `[~]` partially done (see note) · `[ ]` not started

## Core gameplay

- [x] Quick Maths — 20 rapid-fire arithmetic problems, server-graded (`games.py`, `MathGameView.swift`)
- [x] AI Prompt Guess — daily DALL-E-style image, GPT-4o-mini semantic scoring 0-10 (hero game)
- [x] Daily Trivia — 5 daily questions
- [x] Anonymous auth (Supabase) as the identity mechanism, JWT-verified server-side

## Social / competitive

- [x] Global daily leaderboard
- [x] Friends leaderboard + add-by-username
- [x] Streaks (backend: increment/reset logic + `GET /users/me`, now also returns
      today's per-game scores for the share card)
- [~] Streak surfaced in iOS only as a small flame badge in the Leaderboard toolbar —
      no dedicated Profile screen (username, longest streak, history) yet
- [x] Shareable score card — `ScoreCardView.swift` rendered via `ImageRenderer` +
      `ShareLink` from the Leaderboard toolbar, no dedicated Profile screen needed for it

## AI agents

- [x] Daily Content Generator (midnight image prompt + DALL-E image + trivia) — runs
      automatically at local midnight via APScheduler (`app/scheduler.py`), also still
      manually triggerable via `POST /games/generate-daily-content` (admin-token protected)
- [x] Guess Scorer (semantic scoring of player guesses)
- [x] Leaderboard Narrator (daily funny summary, real implementation)

## Design / polish

- [x] Cohesive color theme (`Theme.swift`: indigo accent, adaptive light/dark, card
      containers) applied across Math/Guess/Trivia/Leaderboard/Friends
- [x] Card-style containers for leaderboard rows / game screens
- [~] Small delight moments — trivia option selection pulse + medal-colored ranks done;
      no result-reveal animations yet
- [ ] App icon

## Hardening (pre-launch)

- [x] `pytest` smoke tests for backend routes/agents (`tests/`: root + `/games/test` routes,
      `parse_score` and `parse_trivia_questions` parsing edge cases) — run via `python -m pytest`
- [x] Defensive parsing: `guess_scorer.py`'s `parse_score` now regex-extracts the first number
      instead of crashing on a bare `float()` parse; `content_generator.py`'s
      `parse_trivia_questions` falls back to extracting the outermost `[...]` if the model
      wraps the JSON array in stray prose
- [ ] Dockerfile for backend hosting
- [ ] Pick and configure real hosting for backend (currently local `uvicorn --reload` only)

## Phase (product)

**Phase 1** = web version as a resume/portfolio project. **Phase 2** = eventual iOS App Store launch.
