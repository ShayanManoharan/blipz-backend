# Blipz Implementation Roadmap

Tracks feature status across both repos: `blipz-backend` (FastAPI + Supabase + OpenAI) and
`blipz-ios/Blipz` (SwiftUI). Update the checkboxes as features land — this is the single
source of truth for "what's next," so keep it current rather than trusting memory/plan files.

Legend: `[x]` done and verified · `[~]` partially done (see note) · `[ ]` not started

**Production/App Store readiness:** see `PRODUCTION_AUDIT.md` (2026-07-30) — a full
security/reliability audit found the game scoring is currently exploitable (public endpoint
leaks answers), the backend isn't hosted, and several App Store requirements (account
deletion, privacy manifest, consensual friending) are unimplemented. Read that before treating
anything below as "launch-ready."

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
- [x] Haptics pass complete: added to Leaderboard's Global/Friends segmented switch
      (light) and Friends' add-friend flow (light on tap, success/error on the result),
      rounding out Quick Maths/Guess/Trivia's existing haptics. Every interactive flow
      in the redesign now has tactile feedback.

**Redesign complete** — all items above are done. Remaining polish ideas for a future
pass: real result-reveal confetti/particle effects, a proper avatar/photo system (all
avatars are currently generated initials), and persisting Quick Maths' elapsed time
server-side if speed should ever factor into leaderboard ranking.

## Navigation restructuring — "Today" hub (2026-07-29)

Tabs changed from Maths/Guess/Trivia/Leaderboard/Profile to **Today/Leaderboard/Friends/Profile**.
Friends is now a first-class tab (was previously only reachable via a NavigationLink from
Leaderboard's toolbar — that link was removed as redundant now that it's a tab; no
FriendsViewModel/logic was duplicated, just relocated in the tab list).

- [x] New `TodayView.swift`: daily header (title + date + streak, no reset countdown —
      the backend's day boundary is server-local time with no timezone exposed to the
      client, so there's no real data to count down against), an animated
      "X of 3 completed" progress bar, a prominent hero card for AI Prompt Guess (image
      preview, status, CTA), Quick Maths + Daily Trivia cards below (side-by-side on
      `.regular` horizontal size class, stacked otherwise), a "Today's Total" card using
      `profile.totalScore` exactly as the backend computes it, and a share section that
      gets a stronger "All three completed!" treatment once all 3 games are done (no
      confetti, per constraint).
- [x] Zero changes to `MathGameView`, `GuessGameView`, `TriviaGameView`, or their view
      models — Today pushes them via plain `NavigationLink` + `.toolbar(.hidden, for:
      .tabBar)` (hides the tab bar during gameplay for a focused feel). The nav title
      was later removed in the widget-dashboard redesign below (see that entry).
- **Completion derivation** (documented limitation, not fabricated): Maths uses
  `mathsScore == 20`, a reliable signal since the stopwatch rework guarantees anyone who
  finishes has exactly 20/20. Guess/Trivia use `score > 0` — the only signal that
  exists — which misclassifies a genuine exact-0.0 score as "not played" (rare edge
  case, not the common case). There is no persisted "in progress" state anywhere in the
  backend, so Today only ever shows "Not played" vs. "Completed," never "In progress."
- **Verified in the simulator**: 0-of-3 (real, clean state), 1-of-3, 2-of-3, and 3-of-3
  (including the "All three completed!" share card) by writing/cleaning up real `scores`
  rows for the actual anonymous user behind this simulator install (not fabricated
  data — a genuine `auth.users`-backed account). Also verified iPhone 16e (small device)
  and `accessibility-extra-large` text on iPhone 16e (hardest combination) — layout
  reflows and wraps without clipping. Verified Leaderboard, Friends, and Profile still
  work correctly as standalone tabs post-restructuring.

## Today hub — visual rebuild to "widget dashboard" (2026-07-29)

Redesigned the *existing* `TodayView` in place (no new screen, no architecture change)
to fit as one compact widget-style dashboard instead of a scrolling stack of white
cards. State system tightened to exactly what the data can prove.

- [x] `DailyGameCardState` reduced to `.ready` / `.completed` only — no "in progress"
      case exists anywhere, matching the fact that nothing about an incomplete attempt
      is ever persisted.
- [x] Completion logic centralized as three computed properties on `TodayView`
      (`isMathsCompleted`, `isGuessCompleted`, `isTriviaCompleted`), each with an inline
      comment marking Guess/Trivia's `score > 0` heuristic as a placeholder for future
      explicit `guess_completed`/`trivia_completed` backend fields (backend untouched
      this pass, per instruction).
- [x] Removed the redundant "Today" nav title + "Today's Blipz" double-heading (now
      one compact header), replaced the gray "Not played" pill with a colorful
      violet/indigo "Ready" treatment and a soft-green "Completed" treatment (never
      fully green), added a 3-segment Guess/Maths/Trivia progress indicator with
      animated checkmarks, and gave the hero image a branded shimmer placeholder
      instead of a flat gray box.
- [x] Fits above the tab bar without scrolling at default text size on both iPhone 16e
      and iPhone 17 Pro (header + progress + hero + both compact widgets all visible;
      Today's Total sits just below, share section may need a small scroll — no
      simulated iPhone SE exists in this environment, iPhone 16e is the smallest
      available and stood in for "smaller supported iPhone").
- [x] Reduce Motion respected (shimmer and press-scale animations are skipped, not just
      shortened); Maths/Trivia stack vertically instead of side-by-side once
      `dynamicTypeSize.isAccessibilitySize` is true.
- **Verified live** for all 6 requested states (0-of-3, Guess-only, Maths-only,
  Trivia-only, 2-of-3, all-3) by writing/cleaning up real `scores` rows for the actual
  simulator account, plus iPhone 16e default size and iPhone 16e at
  `accessibility-extra-large`. **Not verified**: actually tapping a card to confirm the
  `NavigationLink` push — no tap-simulation tool is available in this session, so this
  is code-review confidence (a standard `NavigationLink` pointed at an already-working
  screen), not interactive confirmation.

### Follow-up polish pass (2026-07-30)

Second round based on further design feedback — same structure/layout/nav, visual-only:

- [x] Distinct per-game accent colors (`TodayAccent`): Guess stays violet/indigo,
      Maths is electric blue, Trivia is warm amber — applied to icons, borders, and
      "Ready"/"Play" tints so the three widgets read as separate games instead of one
      repeated lavender block. Completed always uses the same soft green regardless of
      game, per spec.
- [x] Progress indicator rebuilt as `DailyProgressTracker`: three icon nodes joined by a
      thin track line (not a shared pill/segmented-control shape), so it can't be
      mistaken for a tappable filter.
- [x] Hero image placeholder now shows a sparkle icon + "Generating today's Blip…"
      copy while genuinely loading (no caption on a load failure, since it already
      tried and isn't "generating" anymore).
- [x] Compact Maths/Trivia widgets get a filled "Play"/"Completed" capsule as the primary
      affordance instead of relying on a small chevron.
- [x] Total/share section is now state-aware instead of a flat "0.0": "Play a game to
      get started" at zero, the real total once 1-2 games are done, and a single
      unified "Daily Blipz complete" + prominent share button at 3-of-3 (previously this
      was two separate cards with redundant "All three completed!" text).
- [x] Wording de-duplicated: page header is now "Today" (was "Today's Blipz"), hero
      label is "DAILY AI GUESS" (was "TODAY'S BLIP") — no more repeated "Blip"/"Blipz".
- **Verified live**: 0-of-3 and all-3 states on iPhone 17 Pro, 0-of-3 on iPhone 16e
  (one-screen fit reconfirmed with the new tracker/spacing). Did not re-run the full
  Accessibility Extra Large / Reduce Motion pass this round since those code paths
  (`dynamicTypeSize.isAccessibilitySize`, `reduceMotion` guards) were untouched —
  only colors and copy changed.

### Branded game emblems (2026-07-30)

Third round: gave each game a small designed identity instead of a bare SF Symbol,
per feedback that completed emblems were losing their per-game color entirely.

- [x] New reusable `BlipzGameEmblem.swift` (in `Theme/`, not Today-scoped, since it's a
      genuine design-system component): a `BlipzGame` enum (guess/maths/trivia) driving
      accent color, primary SF Symbol, an optional small secondary-symbol flourish, and
      accessibility name; `BlipzGameEmblem` renders it as a squircle badge (gradient
      fill, thin white inner border, centered white glyph, small corner-badge flourish)
      that scales purely from a `size` parameter — no per-usage magic numbers.
  - Guess: `photo.fill` + a small `sparkles` corner badge, violet/indigo.
  - Maths: `bolt.fill` (speed-first, not a bare hashtag) + a small `number` corner
    badge, electric blue.
  - Trivia: `questionmark.bubble.fill` alone (already a compound "question in a
    speech bubble" glyph, no secondary needed), warm amber.
- [x] **Completion no longer erases game identity**: the badge's accent color, gradient,
      and glyph never change on completion — the only addition is a thin green ring
      around the whole badge. Guess/Maths/Trivia stay visually distinct even when all
      three are done (this was the core problem with the previous plain-checkmark
      swap-in, now fixed).
- [x] Integrated in exactly three places, as scoped: the hero widget's header (40pt,
      replacing no prior icon — the hero previously had none), each compact widget
      (36pt, replacing the plain icon-in-a-circle), and the progress tracker's three
      nodes (26pt, replacing the plain icon-in-a-circle there too). Removed the
      now-orphaned `CompletionCheckmark` helper since nothing calls it anymore.
  Also moved `TodayAccent` out of `TodayView.swift` (was `private`, so it wasn't
  visible to the new emblem file) into `BlipzGameEmblem.swift` as the shared,
  non-private source of truth for all three accent colors.
- [x] `#Preview("Emblem grid")` added to the component file showing all 3 games at 3
      sizes (30/36/52) in both ready and completed states, for Xcode-canvas iteration —
      **this specific preview was not screenshotted**, since Xcode Previews render in
      Xcode's canvas, which isn't reachable through the simulator-based screenshot
      tooling available in this session. Live verification instead happened directly
      against the real `TodayView` in the running simulator (see below).
- **Verified live**: 0-of-3 (all three Ready, distinct colors) and all-3-completed
  (all three retain their base color + green ring) on iPhone 17 Pro; one-screen fit
  reconfirmed on iPhone 16e at 0-of-3; **light and dark mode** both checked on
  iPhone 16e via `simctl ui <device> appearance` — emblems stay legible and vibrant
  against the dark background.
- **Incidental fix, not part of this task**: discovered while testing that no
  `daily_content` row existed for today (2026-07-30) — the local machine slept
  overnight, so the midnight `APScheduler` job never fired (it can't run while the
  process itself is suspended). Seeded a placeholder row so the app has content today;
  this is exactly the local-hosting limitation already tracked below.

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
