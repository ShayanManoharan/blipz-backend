# Blipz V2 — concrete contract proposal

Status: UNCOMMITTED REVIEW PROPOSAL. Product choices explicitly locked by the user are requirements. Timing, ranking tie policy, minimum population, rollover, and client cutover behavior below are concrete recommendations awaiting approval. No code, migration, live data, or iOS implementation is included.

## A. Exact domain contracts

`contract.schema.json` is the exact JSON representation. Named definitions below resolve as `#/$defs/<Name>`. All fields are required unless their definition explicitly permits null; unknown fields are rejected. Relational constraints and conditional invariants below supplement structural JSON validation.

| Requested model | Exact definition | Visibility |
|---|---|---|
| 1. DailyChallenge | `DailyChallenge` | Authenticated players; lineup summaries only |
| 2. DailyGameInstance | `DailyGameInstance` | Player-safe instance identity/configuration |
| 3. GameContentRevision | `GameContentRevision` | INTERNAL ONLY; contains private solution |
| 4. Hidden Rule public | `HiddenRulePublicPayload` | Only after authoritative attempt start |
| 5. Hidden Rule private | `HiddenRulePrivateSolution` | Backend/editorial only |
| 6. Riddle public | `RiddlePublicPayload` | Only after start |
| 7. Riddle private | `RiddlePrivateSolution` | Backend/editorial only |
| 8. Trivia public | `TriviaPublicPayload` | Only after start; native choices included |
| 9. Trivia private | `TriviaPrivateSolution` | Backend/editorial only |
| 10. World Guess public | `WorldGuessPublicPayload` | Only after start |
| 11. World Guess private | `WorldGuessPrivateSolution` | Backend/editorial only |
| 12. DailyGameAttempt | `DailyGameAttempt` | Owner/backend only; never another player's raw answer |
| 13. DailyResult | `DailyResult` | Owner; public ranking uses separate projection |
| 14. DailyActivity | `DailyActivity` | Internal ledger, not a leaderboard payload |
| 15. Completed review | `CompletedGameReview` | Owner after final completion |
| 16. Overall leaderboard row | `OverallLeaderboardRow` | Authenticated players; public player identity |
| 17. Per-game leaderboard row | `GameLeaderboardRow` | Authenticated players; no answer text |
| 18. Per-game rank/stat summary | `GameRankStatSummary` | Current player, exact full-cohort computation |
| 19. History row | `HistoryRow` | Current player |

Supporting definitions specify exact choices, assets, normalization policy, answer union, result contribution, progress, start, reveal, submit, and error envelopes. `PublicGameContentRevision` is the only player-safe revision projection; never serialize internal `GameContentRevision` into a public response.

### Identity, versions, and snapshots

- UUID identities are immutable. A game instance belongs to exactly one challenge and references one exact content revision. Attempt `user_id`, challenge, instance, revision, game type, and UTC challenge date must agree with those authoritative records.
- Launch order: Hidden Rule, Riddle, Trivia, World Guess. Positions are unique and contiguous; maximums are 2500 each and sum to 10000. Exactly four required instances for this scoring version.
- Product version: `blipz-v2`; contract version: `v2.1` (first proposal identifier, not a shipped version); scoring: `v2-equal25-v1`; ranking: `v2-rank-v1`.
- Generic database columns should use versioned identifiers/text, not hardwired four-game columns. The launch schema's four-instance/25-point constraints describe this version; a future contract may extend them without adding game-specific columns.
- `GameContentRevision.variant` is null except World Guess's launch `flag` or `outline`. Internal and public `kind`/variant must agree. A variant extension requires a typed validator/renderer and contract version, not a new attempts table.
- Revision content hash: SHA-256 of UTF-8 JSON for `{public_payload, private_solution}`, keys sorted, no insignificant separators, Unicode preserved. A byte/order change to arrays changes identity. Asset hashes independently cover actual bytes.
- Public asset names, URLs, alt text, SVG metadata, and thumbnails must not encode answers. Private editorial provenance may identify them. Published hashes alone are not authorization; private solution data is not published.
- Publication requires successful current validators AND recorded human approval for each revision. All local fixtures deliberately lack that approval. V2 immutable revisions do not reuse V1 Guess-specific required fields.

### Answers and grading

`FinalAnswer` has exactly one of these shapes:

```json
{"kind":"typed","text":"divisible by 3"}
{"kind":"choice","selected_option_id":"B"}
{"kind":"trivia","answers":[{"question_id":"q0","selected_option_id":"B"},{"question_id":"q1","selected_option_id":"C"},{"question_id":"q2","selected_option_id":"B"},{"question_id":"q3","selected_option_id":"B"},{"question_id":"q4","selected_option_id":"D"}]}
```

Normalization v1: NFKC, Unicode casefold, collapse whitespace, then strip terminal `.`, `!`, `?`; no spelling guesses, semantic model, or unbounded fuzzy matching. Accepted aliases are normalized by the same function. Approved patterns, if ever used, must use a restricted anchored grammar with bounded input and safe runtime; local examples use aliases only. Regex/pattern meaning must be versioned identically across validation and grading. An unfamiliar but syntactically valid final phrase is wrong, not a free trial. Editorial review must ensure supported phrasing is reasonable before publication.

Hidden Rule launch example supports a private `integer_divisible_by` specification. No arbitrary code/eval is allowed. The domain and predicate are explicit. Every positive/negative example must match its declared label; each distractor predicate must conflict with at least one example. Reject duplicate/contradictory samples and semantically equivalent choices within the declared finite domain. Future rule families need dedicated implementations and validation. Human review remains mandatory because no finite examples prove uniqueness among every imaginable rule.

Riddle stores a canonical answer, approved aliases, four choices, and explanation. Trivia has exactly five distinct question IDs, four distinct choices per question, and one private correct ID per question. Submission must contain each expected ID exactly once. World Guess stores a canonical country/entity ID, alias list, dataset version, four choices, and source metadata. Launch supports only flag/outline; neither contains AI-based live grading.

### Attempts, completion, and progress

- `not_started` is a virtual state returned in progress, not a database row created by GET. `DailyGameAttempt` describes a persisted started attempt and therefore excludes `not_started`. Its identity and `started_at` are always non-null after creation.
- Before final completion: final answer, final answer mode, correctness, counts, awarded points, score percentage, `completed_at`, elapsed duration, and completion key are null. Remaining maximum is 2500 or 1875.
- A typed game's terminal counts are 1/1 or 0/1. Trivia counts are 0..5 out of 5. `is_correct=true` means full accuracy; `completed_incorrect` for Trivia can still have partial credit (1..4 correct). UI should label this simply Completed rather than Failed.
- `accuracy_bps` measures correct answers, independent of assistance. `score_percentage_bps` measures awarded/max points; an assisted correct solve has accuracy 10000 and score percentage 7500.
- Native Trivia has `answer_mode=native_mcq`, `choices_revealed=false`, and no reveal timestamp or penalty.
- `DailyResult.games` contains only terminal game contributions. Count and total are derived from those contributions. `total_elapsed_ms` sums completed games' elapsed durations, excluding gaps between games. Overall eligibility is true iff every required instance is terminal. `completed_at` is null until that point, then equals the latest game completion time.
- `expired` is a read-time projection for an unfinished started attempt after the deadline; it does not manufacture a completion, score, streak, or ledger event. No background write is required merely to render expiry. A never-started closed game remains not_started with challenge closure indicated by the challenge deadline.

## B. Exact V2 endpoint proposal

All routes below are PROPOSED, not implemented. Prefix `/v2`. Every route requires a valid Supabase-authenticated user session (existing anonymous guest sessions count); mutations are performed only by the backend. No service/admin key ships to iOS. An owner is resolved from the token, never accepted from request JSON. All requests/responses are JSON except bodyless GETs. Standard errors use `Error`: `{error:{code,message,retryable,request_id}}`, with no private payloads.

Every route: 401 invalid/missing session; 429 rate limit; 503 unavailable dependency. Unknown or inaccessible challenge/instance/attempt identities return 404. Recognized but unsupported client contract gets 426 `client_update_required` before a mutation. Invalid path/query/body or unknown fields get 422; invalid cursor gets 400.

### 1. Fetch today's challenge

`GET /v2/challenges/today` — no body.

200: `{ "challenge": DailyChallenge, "server_time": UTC_TIMESTAMP }`.

Read-only/idempotent. Includes titles, instance IDs, positions, weights, and policy, but no clues, image URLs, answer aliases, optional choices, or solutions. 404 `challenge_unavailable` if no published challenge; never returns a draft. Uses UTC. Private caching only, with normal authentication; never shared-cache owner data.

### 2. Fetch current progress

`GET /v2/challenges/{challenge_id}/progress` — no body.

200: `DailyProgress` exactly. Four `ProgressSlot`s include virtual not_started states without inserting rows. For expired/incomplete slots, result is null. Read-only/idempotent. May read a historical challenge's own progress without enabling submissions.

### 3. Start or reopen an attempt (required additional endpoint)

`POST /v2/game-instances/{instance_id}/start`

Request: `{ "idempotency_key": UUID }`.

200: `StartAttemptResponse`. The returned `content` is `PublicGameContentRevision`, never the internal object.

The first successful call atomically creates/starts the owner/instance attempt and records database time BEFORE returning clues or asset URLs. Repeated calls return the same start, revision, and attempt; the timer never resets. An already completed call during the open challenge returns `already_completed=true` and the existing result state, which iOS renders as review. 409 `challenge_not_open`; 410 `challenge_closed`. Fetching lineup/progress never starts timers. This endpoint is necessary: timing cannot honestly start only when a final answer is submitted.

### 4. Reveal optional choices

`POST /v2/game-instances/{instance_id}/choices/reveal`

Request: `{ "idempotency_key": UUID }`.

200: `RevealChoicesResponse`. Contains four `Choice` objects, reveal timestamp, and cap 1875; no correct ID or private rule specification.

Requires active attempt. First reveal persists state/cap atomically; duplicates return the same choices/timestamp with `already_revealed=true`. Choice IDs/order are stable for the instance, not freshly randomized per request. 409 `attempt_not_started`, `attempt_completed`, or `choices_not_supported` (Trivia); 410 `challenge_closed`. No implicit start.

### 5. Submit one final answer

`POST /v2/game-instances/{instance_id}/submit`

Request: `{ "idempotency_key": UUID, "answer": FinalAnswer }`.

200: `SubmitResponse`. This is the sole operation that awards score or daily activity.

Active typed game accepts typed only; choices_revealed accepts choice only; Trivia accepts the five-answer set only. Wrong valid answers complete the game at zero (or partial aggregate Trivia score). 409 `attempt_not_started` or `answer_mode_mismatch`; 422 malformed/duplicate/missing/foreign question or option IDs; 410 closed unfinished attempt. Invalid request shape/IDs do not consume a final submission and do not disclose correctness.

Once final, subsequent submissions return the ORIGINAL attempt/result with `already_completed=true`, regardless of a different new key; no regrading, score, timing, or streak change. Reusing the SAME idempotency key with a different canonical request body returns 409 `idempotency_key_reused`. A same-body retry of an accepted completion remains retrievable after midnight. Idempotency records are owner+route+instance scoped; request fingerprints are private.

### 6. Fetch protected completed review

`GET /v2/game-instances/{instance_id}/review` — no body.

200: `CompletedGameReview`. Read-only/idempotent; exact attempt revision, owner only, available for historical completed games as well. 403 `review_locked` if no authoritative terminal attempt. Expiry alone does not unlock review. Returns player-facing answer/explanation, not the entire internal solution, accepted-alias list, or executable rule.

### 7. Fetch daily result

`GET /v2/challenges/{challenge_id}/result` — no body.

200: `{ "result": DailyResult, "server_time": UTC_TIMESTAMP }` including in_progress with zero contributions if untouched. Read-only/idempotent. Reveal becomes available only when the result is completed; an incomplete result is not a final Reveal.

### 8. Overall leaderboard

`GET /v2/challenges/{challenge_id}/leaderboard?limit=50&cursor=OPAQUE` — no body; limit 1..100.

200: `{ "challenge_id": UUID, "ranking_version":"v2-rank-v1", "as_of":UTC_TIMESTAMP, "eligible_population":INTEGER, "rows":[OverallLeaderboardRow], "self":OverallLeaderboardRow|null, "next_cursor":STRING|null }`.

Only fully completed daily results qualify. `self` is computed over the entire eligible population, even outside the page. Read-only, no cache or generation writes. Signed cursor binds challenge, snapshot cutoff, ranking version, page size, and last ordering key. First page chooses the cutoff; subsequent pages keep it. This is stable because completed results are immutable. Invalid/expired cursors: 400 `invalid_cursor`/`cursor_expired`; suggested cursor TTL 10 minutes. No final answer text exposed.

### 9. Per-game leaderboard

`GET /v2/game-instances/{instance_id}/leaderboard?limit=50&cursor=OPAQUE`

200: `{ "challenge_id":UUID, "game_instance_id":UUID, "ranking_version":"v2-rank-v1", "as_of":UTC_TIMESTAMP, "eligible_population":INTEGER, "rows":[GameLeaderboardRow], "self":GameLeaderboardRow|null, "next_cursor":STRING|null }`.

Same auth, pagination, cursor failures and read semantics as overall. Only completed authoritative attempts for this exact instance qualify, including incorrect completions; unfinished/expired attempts do not.

### 10. Per-game rank/percentile summary

`GET /v2/game-instances/{instance_id}/rank-summary`

200: `GameRankStatSummary`. Includes exact ordinal rank, performance rank, full eligible count, tie count, and optional percentile. Incomplete user: eligible=false, ranks/percentiles/counts relative to user are null, reason=not_completed. Small cohort: exact rank remains available, percentile fields null. Read-only/idempotent at a fixed snapshot, but new completions may change live rankings.

### 11. History

`GET /v2/users/me/history?limit=30&cursor=OPAQUE` — no body; limit 1..100.

200: `{ "rows":[HistoryRow], "next_cursor":STRING|null }`.

Descending UTC date with stable product/version/ID tie key and owner-bound cursor. Read-only adapter over legacy and V2 storage; no historical rescore or backfill. Returns score-bearing/started days rather than invented empty days. No private solutions. Invalid cursor: 400. Legacy rows use games=[] and nullable completion metadata where old records cannot establish it honestly; never infer completed solely from score>0.

## C. Attempt state machine and authoritative timing

| State | Action | Outcome |
|---|---|---|
| not_started | start | active; persist attempt/start once |
| active | start again | same active attempt/start/content |
| active, compatible typed game | reveal choices | choices_revealed; timestamp and 1875 cap persist |
| choices_revealed | reveal again | unchanged; same choices/timestamp |
| active, compatible typed game | typed final | completed_correct / completed_incorrect |
| choices_revealed | choice final | completed_correct / completed_incorrect |
| active, Trivia | trivia final | completed_correct for 5/5, otherwise completed_incorrect; partial points preserved |
| terminal | start while open | existing completed state; never active |
| terminal | submit retry/new submission | original completion, subject to same-key/different-body rejection |
| terminal | reveal | 409 attempt_completed |
| not_started | reveal/submit | 409 attempt_not_started |
| active | choice without reveal | 409 answer_mode_mismatch |
| choices_revealed | typed final | 409 answer_mode_mismatch; cap stays reduced |
| active/choices_revealed | UTC deadline reached | effective expired; no awarded score/activity |
| expired | new start/reveal/submit | 410 challenge_closed |

No reverse transition exists. There is no wrong-typed -> MCQ retry. Completion events are immutable, even when worth zero.

Transaction recommendation: take locks in consistent order user -> daily result -> attempt; create missing rows with uniqueness protection. Resolve mutation identity, lock, read fresh state, obtain database wall-clock timestamp, recheck deadline/state/mode, and apply grading/cap/completion/aggregate/activity as one transaction. Validation can be prepared outside the lock only if final grading references the locked immutable revision. Rolls back completely on any error.

- Reveal wins lock first: cap 1875 persists; concurrent typed submit is rejected. A subsequent permitted choice answer earns at most 1875.
- Typed submit wins first: final score locks at 2500 or 0; concurrent reveal returns attempt_completed with no choice content.
- Two submissions: only one final result; later call returns original. Idempotency keys cannot reset timing or replace answers.
- Repeated start cannot reset timing; completion and reveal require an already started attempt.

Server timing proposal: `started_at` comes from database wall clock after acquiring the start lock. `completed_at` comes from database wall clock inside the successful final transaction after locking and checking state. `elapsed_ms=floor((completed_at-started_at)*1000)`, integer >=0. Use neither client elapsed nor transaction-start `now()` when a transaction waited on locks. Unhealthy/backward clock movement must fail safely and alert, not clamp into an artificially fast score.

Wall-clock duration includes background time, network delay, lock wait, and choice deliberation. There is no pause/resume timer or minimum speed bonus. Total daily time is the sum of per-game elapsed times, not time since first game. These measures are authoritative elapsed wall time, NOT a proof of active thinking or anti-cheat guarantee. Multi-account prelearning and shared puzzles remain possible; do not promise stronger protection.

UTC opens_at inclusive, closes_at exclusive. Recommended deadline is next UTC midnight; no new completion after closure. Accepted final retries and completed reviews remain readable afterward. This rollover policy requires approval.

## D. Scoring examples

| Case | Accuracy | Assistance | Awarded units | Points |
|---|---|---|---:|---:|
| Typed correct | 1/1 | none | 2500 | 25.00 |
| MCQ correct after reveal | 1/1 | irreversible choices | 1875 | 18.75 |
| Wrong typed final | 0/1 | none | 0 | 0.00 |
| Wrong assisted final | 0/1 | choices | 0 | 0.00 |
| Trivia 5/5 | 100% | native MCQ; no penalty | 2500 | 25.00 |
| Trivia 4/5 | 80% | native MCQ; no penalty | 2000 | 20.00 |

Example completed day: Hidden Rule typed 2500 + Riddle assisted 1875 + Trivia 4/5 2000 + World Guess typed 2500 = 8875 units = 88.75/100. Elapsed sum is 150000ms. All three optional-choice games assisted plus perfect Trivia would total 8125 = 81.25/100.

No hint state, timing multiplier, or speed-based point bonus. Speed orders otherwise equal performance; it never changes earned points. Display precision must preserve hundredths in scoring/rank comparisons and shares; do not rank rounded display strings.

## E. Ranking and honest percentiles

Eligibility: exact published challenge/instance, authoritative final record, supported scoring/ranking versions. Game includes both correct and incorrect terminal attempts; overall requires ALL required instances terminal (zero-point completion counts). Started/expired rows are not eligible. No cross-date, cross-version, or different-instance mixing.

Deterministic ordering:

- Hidden Rule/Riddle/World Guess: awarded_points_units DESC, choices_revealed ASC, elapsed_ms ASC, completed_at ASC, attempt.id ASC.
- Trivia: correct_count DESC, elapsed_ms ASC, completed_at ASC, attempt.id ASC.
- Overall: total_points_units DESC, total_elapsed_ms ASC, completed_at ASC, daily_result.id ASC.

For a single instance, equal maximums make points order equivalent to awarded percentage order. Accuracy always dominates speed. Correct typed 40s outranks correct assisted 8s; Trivia 5/5 at 90s outranks 4/5 at 8s. Incorrect typed and incorrect assisted both score zero; assistance orders them before speed, per the proposed key. No global comparison across game types.

`rank` is an exact unique ordinal (row_number over the full deterministic key). `performance_rank` is competition rank (1 + strictly better count) over meaningful performance keys ONLY: points/assistance/elapsed for typed games; correct_count/elapsed for Trivia; total points/total elapsed for overall. Equal performance does not cease to tie merely because one completed earlier or has a lower UUID. completed_at and ID only establish stable display order.

For game percentiles, use the FULL eligible population at one consistent database snapshot, not the returned page. Let N=population, B=strictly better by performance key, T=number sharing the user's performance key (including self), W=N-B-T.

- percentile_bps = floor(10000 * W / N): conservative percentage strictly behind the user.
- top_percent_display = ceil(100 * (B+T) / N): conservative tied-group upper bound, shown as Top X%.
- Recommend minimum N=100 before exposing either percentile field; exact rank is still available below 100. Suppressed values are null, never zero placeholders.
- N=1000, B=60, T=10 -> percentile 9300 bps, Top 7%.
- N=100 and everyone tied -> percentile 0 bps, Top 100%, not a misleading Top 1%.
- N=99 -> no percentile display.

Live summaries carry as_of and may change as more users finish. History must distinguish live ranking from finalized daily ranking. For future stable Top 10% statistics, compute against the closed-day eligible population/ranking version, not an early live screenshot. No all-time ranking endpoint or percentile storage/backfill is needed now; retained attempt telemetry and immutable instances support future solve-rate, full-credit-rate, time, and percentile aggregation. New analytics/index migrations may still be desirable for scale, but no new game-specific telemetry columns should be required.

## F. Local fixtures and verification scope

Five content revisions are in content.json; the example four-game challenge uses the flag variant. The outline is an alternative World Guess revision, not a fifth daily game. Fixture approval is deliberately not_reviewed. Sample negative subtraction/math content is absent because Maths is preserved V1, not a V2 slot.

Hidden Rule: integer domain 1..99, divisor 3. Positives 6,12,21,30 yield remainders 0,0,0,0. Negatives 7,14,22,31 yield 1,2,1,1. Distractors divisible by 2/5/10 each contradict the labels. Local checks enumerate every integer in the domain to rule out equivalent MCQ predicates, and mutate a negative to 9 to prove rejection. This proves consistency and distractor distinction within this domain, not universal uniqueness of natural-language interpretations.

Gameplay fixtures demonstrate typed/assisted/wrong results, equal-score time ordering, Trivia accuracy before speed, a full 88.75 day, once-per-day activity, protected review identity, suppressed small-cohort percentile, tie-safe percentile arithmetic, and legacy history shape. They are not real backend records or production observations.

## G. Compatibility and activity bridge

Do not write V2 results into legacy scores or replace old content/fallback/replacement rows. Existing V1 branches/tags and history remain unchanged. Read adapters expose old /35 and normalized V1 /100 as their own scoring versions; multiply stored values by 100 for transport units without altering storage or recomputing their formulas. Legacy missing breakdown/review data remains unavailable, not reconstructed.

History completion fields may be null where old data cannot prove status. No score>0 completion inference. V2 games list is genuinely instance-based. Generic Today/Reveal consume server instance order, completion, and contributions; they must not derive a missing contribution from other games.

First terminal game on a UTC date, including zero points, records DailyActivity and advances streak once; later games cannot advance it. Uniqueness is (user_id,date), across product versions. The completion transaction updates result + ledger + user streak together. Yesterday may be established by a V2 ledger row OR existing legacy completion flags; historical ledger backfill is unnecessary.

Critical cutover constraint: the existing V1 completion RPC does not consult a V2 ledger. Therefore do NOT allow concurrent competitive V1/V2 submissions for the same UTC date while claiming the ledger alone prevents double advancement. Recommended launch policy: one competitive product version per UTC day, enforced server-side before legacy or V2 mutation. Once V2 opens, older clients receive update_required for new competitive submissions; historical V1 reads remain supported. Alternative dual-running requires an explicitly approved coordination change, not just new tables. No such change is made here.

## H. Decisions still requiring approval

1. Server wall-clock timing includes network/background time; start reveals clue payload, not Today fetch. No pause.
2. Midnight closes unfinished attempts; no cross-midnight grace completion.
3. Exact ordinal rank + performance-tie percentile, minimum cohort 100, conservative Top X% formula.
4. Proposed overall time tie breaker is sum of individual game durations, excluding between-game gaps.
5. One competitive version per UTC day; older-client update policy at cutover.
6. Nonvisual equivalent for flag/outline and Hidden Rule examples: neutral alt text avoids spoilers but is insufficient for equivalent accessible gameplay. Choose an equitable mode before launch; do not silently disclose answers.
7. Editorial policy for countries/territories, aliases, outline islands and granularity, and acceptable riddle/rule phrasing. Fixture content is not publication approval.
8. How completed historical Reveal displays final versus provisional ranks. Proposed policy: rankings after UTC closure are final for this ranking version unless separately audited disqualification is introduced; no moderation/exclusion system is assumed in this first contract.

## I. Proposed first implementation commit after approval

`add V2 domain contracts and pure validation tests`

Scope: typed backend request/response/domain models in a separate V2 module; integer scoring helpers; supported Hidden Rule predicate/alias normalization; pure validator tests with local fixtures and negative cases. No router registration, iOS screens, application startup changes, database connections/migrations, production generation, or deployment. Keep these docs/fixtures as review material until explicitly approved for inclusion. Prepare schema/RPC implementation in later independently reviewed commits, with isolated database tests before any shared-environment application.
