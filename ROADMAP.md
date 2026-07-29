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
- [x] Streaks (backend: increment/reset logic + `GET /users/me`)
- [~] Streak surfaced in iOS only as a small flame badge in the Leaderboard toolbar —
      no dedicated Profile screen (username, longest streak, history) yet
- [ ] Shareable score card (Wordle-style, one-tap share of daily results)

## AI agents

- [x] Daily Content Generator (midnight image prompt + DALL-E image + trivia) — currently
      triggered manually via `POST /games/generate-daily-content` (admin-token protected)
- [x] Guess Scorer (semantic scoring of player guesses)
- [x] Leaderboard Narrator (daily funny summary, real implementation)
- [ ] Scheduler so the Daily Content Generator actually runs at midnight automatically
      (APScheduler via FastAPI lifespan, or platform cron hitting the admin endpoint)

## Design / polish

- [ ] Cohesive color theme + typography (currently default SwiftUI styling throughout)
- [ ] Card-style containers for leaderboard rows / game screens instead of plain lists/VStacks
- [ ] Small delight moments (result animations, streak flame animation, etc.)
- [ ] App icon

## Hardening (pre-launch)

- [ ] `pytest` smoke tests for backend routes/agents
- [ ] Defensive parsing: `guess_scorer.py`'s bare `float()` parse, `content_generator.py`'s
      markdown-fence-stripped `json.loads`
- [ ] Dockerfile for backend hosting
- [ ] Pick and configure real hosting for backend (currently local `uvicorn --reload` only)

## Phase (product)

**Phase 1** = web version as a resume/portfolio project. **Phase 2** = eventual iOS App Store launch.
