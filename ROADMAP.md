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
- [x] Dedicated Profile tab (`ProfileView.swift`) showing username, current/longest streak,
      and today's per-game scores, in addition to the existing Leaderboard toolbar badge
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
- [x] Small delight moments — trivia option selection pulse, medal-colored ranks, and a
      spring scale+fade reveal when Maths/Guess/Trivia results appear
- [x] App icon — a simple "blip" (radar ring + dot) mark on an indigo gradient, generated
      programmatically (Pillow, zero-cost) with light/dark/tinted appearance variants,
      verified rendering correctly on the simulator home screen

## Visual redesign — "playful premium daily arcade" (in progress, started 2026-07-29)

Extending the existing `Theme.swift`/`PrimaryButtonStyle`/`ResultCard` system (not
replacing it) toward a stronger electric-violet identity, staged one screen at a time.

- [x] Quick Maths reworked: 20 problems shuffled into a random per-player order, a
      "Play" button that starts a live stopwatch (`TimelineView`), auto-advance on a
      correct answer (no Submit button — wrong stays on the same question), result
      screen shows completion time. `maths_score` still always grades 20/20 for anyone
      who finishes (unchanged backend contract) — elapsed time is client-side only,
      not persisted or factored into leaderboard ranking yet
- [x] AI Prompt Guess redesign (hero screen): violet glow behind the artwork card,
      "AI is judging your guess…" interim state, animated 0→score count-up via
      `.contentTransition(.numericText)`, a completed state with a share button
      (reuses the shared `ScoreCardRenderer` extracted from Leaderboard's share code)
- [x] Daily Trivia redesign: header + progress bar, tactile option cards that clearly
      reveal correct (green) / incorrect (red, dims the rest) on selection with
      success/error haptics, 550ms pause before advancing so the reveal is visible
- [x] Global fix: `screenBackground()` now top-aligns content instead of centering it
      in the middle of the screen — addresses "reduce excessive unused vertical space"
      for every screen at once (Theme.swift change, not per-screen)
- [x] Leaderboard redesign: top-3 podium (SF Symbol rank badges, not emoji) shown when
      3+ players exist, initials-avatar circles on every row, current-user row
      highlighted + "You" tag (matched by username, no backend change), narrator message
      restyled as a "Daily Commentary" card, empty states for both Global/Friends scopes.
      Verified live against the real single-player leaderboard (non-podium path); the
      3-player podium branch is code-reviewed but not visually verified — the dev DB
      only has one real player today and inserting fake rows would need fake
      `auth.users` rows to satisfy the FK, so I didn't fabricate data for a screenshot
- [x] Profile redesign: emoji swapped for SF Symbols throughout (flame/trophy/number/
      photo/questionmark icons), each game row now shows an icon + progress bar + score
      + a completion checkmark (only for Maths, where 20/20 is a reliable "finished"
      signal post-rework — Guess/Trivia can legitimately score 0 without meaning
      "not played," so no false checkmark there), a "Today's Total" card, and a
      share-results button gated on `totalScore > 0`. Screenshot-verified.
- [x] Tab bar polish: verified on iPhone 16e (smallest relevant simulator) that iOS 26's
      native `TabView` chrome already renders as a soft elevated floating pill with
      correct violet active-tint and no label crowding (all 5 labels, including
      "Leaderboard", fit without truncation) — decided not to build a custom tab bar
      since the system default already satisfies the brief and a custom one would risk
      breaking swipe/accessibility/Dynamic Type behavior that comes free natively
- [x] Dynamic Type pass: converted every fixed-`size:` "hero" font (screen headers, the
      Maths equation, the Guess/Profile score displays, `ResultCard`'s title) to
      `@ScaledMetric` so they actually grow with the user's text size setting instead of
      staying pixel-locked; added accessibility labels/grouping to headers, the Maths
      stopwatch, and icon-only toolbar buttons (share, streak badge). Verified live by
      setting the simulator to `accessibility-extra-large` via
      `xcrun simctl ui <device> content_size` — text visibly scales up and layouts
      reflow without clipping. Icon-only sizes (avatars, symbols) were left fixed —
      text legibility was the actual accessibility concern, not icon dimensions
- [ ] Haptics pass across remaining screens (`Haptics.swift` helper added, used in
      Quick Maths, Guess, and Trivia so far)

## Hardening (pre-launch)

- [x] `pytest` smoke tests for backend routes/agents (`tests/`: root + `/games/test` routes,
      `parse_score` and `parse_trivia_questions` parsing edge cases) — run via `python -m pytest`
- [x] Defensive parsing: `guess_scorer.py`'s `parse_score` now regex-extracts the first number
      instead of crashing on a bare `float()` parse; `content_generator.py`'s
      `parse_trivia_questions` falls back to extracting the outermost `[...]` if the model
      wraps the JSON array in stray prose
- [x] Dockerfile for backend hosting — verified with a real `docker build` + `docker run`
      against `.env`, confirmed `/` responds and the scheduler starts inside the container
- [ ] Pick and configure real hosting for backend (currently local `uvicorn --reload` only) —
      user opted to stay local for now (2026-07-29), revisit when ready to deploy

## Phase (product)

**Phase 1** = web version as a resume/portfolio project. **Phase 2** = eventual iOS App Store launch.
