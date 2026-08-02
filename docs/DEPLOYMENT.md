# Blipz Backend — Deployment Guide

Companion to `PRODUCTION_AUDIT.md`. This covers hosting, environments, the daily-content
pipeline, and the exact steps to deploy, verify, and roll back. **No resource described
here has been created — this is a plan plus the repository-side changes needed to act on
it, pending your approval to actually deploy.**

---

## 1. Hosting comparison

Compared: **Render**, **Railway**, **Fly.io** — all three are commonly recommended for a
small FastAPI + Docker app with no existing infra investment.

| | Render | Railway | Fly.io |
|---|---|---|---|
| Docker/FastAPI support | Yes, native | Yes, native | Yes, native |
| HTTPS | Automatic, free | Automatic, free | Automatic, free |
| Scheduled jobs | Native **Cron Job** resource type (its own service, runs a command on a schedule) | Cron schedule field on any service | No built-in cron — needs an external trigger or a scheduled Fly Machine |
| Scale-to-zero | Free tier only; **Starter** tier ($7/mo) is always-on | Doesn't scale to zero on paid usage-based plans | Configurable — can auto-stop/start machines, or run always-on |
| Cold starts | None on Starter+; ~30-60s on free tier | None in practice on a normal paid service | Sub-second to a few seconds if machines auto-stop; none if always-on |
| Persistent worker reliability | Solid on Starter+ | Solid | Solid, more manual control (you manage machine count/regions) |
| Env/secret management | Dashboard UI + `render.yaml` (secrets marked `sync: false`, entered manually) | Dashboard UI, per-service variables | Dashboard UI + `fly secrets set` |
| Logs | Built-in dashboard log stream | Built-in dashboard log stream | Built-in (`fly logs`) + dashboard |
| Expected cost, small launch | ~$7/mo (Starter web service) + cron jobs are typically low/no additional cost at this scale | Usage-based; roughly comparable to Render at this traffic level, historically less predictable pricing | Often the cheapest for one small always-on instance (fractional-vCPU machines), but requires more CLI-driven setup |
| Ease of deployment | Easiest — connect GitHub repo, `render.yaml` blueprint, auto-deploy on push | Also easy — GitHub integration, minimal config | More CLI-oriented (`flyctl launch`/`fly deploy`, `fly.toml`) — same end result, more manual steps |
| Compatibility with Supabase/OpenAI | Trivial — outbound HTTPS only, no meaningful difference across any of the three | | |

**Recommendation: Render.** The app is a single small FastAPI service with no unusual
infra needs, and ease of deployment + a native Cron Job resource type (which maps
directly onto "generate ahead of time, publish at the boundary") matter more here than
saving a few dollars a month. `render.yaml` is included in this repo (see below) and
already models both the web service and the two cron jobs.

**Estimated small-launch cost:** ~$7/mo for the Starter web service (avoids cold starts,
which matter for a game people expect to load instantly). The two cron jobs are
lightweight (each just does an HTTPS POST) and add negligible cost at Render's per-run
pricing for small jobs.

If cost becomes the dominant concern later, Fly.io is the natural second choice — same
architecture, just swap `render.yaml` for a `fly.toml` and use an external free cron
service (e.g. a GitHub Actions scheduled workflow, or any HTTPS-capable cron) to hit the
same `/admin/generate-content` and `/admin/publish-content` endpoints, since Fly.io has
no native cron primitive of its own.

---

## 2. Environment strategy

Three environments, one codebase, driven entirely by env vars (`app/config.py`):

| | Local development | Staging / TestFlight | Production |
|---|---|---|---|
| `ENVIRONMENT` | `development` | `staging` | `production` |
| Backend | `uvicorn --reload` on your Mac | Render web service (or a second Render service) | Render web service |
| In-process scheduler | **On** (developer convenience — see `app/main.py`) | Off | Off |
| Daily content | Generated/published by the local scheduler or manual `POST /games/generate-daily-content` | Render Cron Jobs hit `/admin/generate-content` + `/admin/publish-content` | Same |
| `CORS_ALLOWED_ORIGINS` | Empty, or `http://localhost:*` if you add local web tooling | Empty (native iOS only) | Empty |
| iOS build config | Debug | Staging | Release |
| iOS `apiBaseURL` | `http://127.0.0.1:8000` | Staging backend HTTPS URL | Production backend HTTPS URL |
| Supabase project | Existing dev project | Same project (no separate staging Supabase project exists yet — see note below) | Same project |

**Note on Supabase environments:** this app currently has one Supabase project. A fully
isolated staging Supabase project (separate auth users/data) is good practice before a
real public launch, but creating one is itself a resource-creation decision — not done
here without your approval. Until then, staging and production share the same Supabase
project; be aware that staging/TestFlight testing writes real rows into the same tables
production will read.

---

## 3. Daily-content pipeline

See `PRODUCTION_AUDIT.md`'s deployment section and `app/agents/content_generator.py` for
the full design. Summary:

- **`generate_content_for_date(content_date)`** — idempotent. Produces a fully
  validated package (image generated, uploaded, upload verified reachable; all 5 Trivia
  questions validated; math problems built) entirely in memory, and only then writes
  one row with `status='ready'`. A failure at any point raises before anything is
  written — no partial package is ever stored. Calling it again for an already
  `ready`/`published` date is a no-op (no OpenAI calls).
- **`publish_content_for_date(content_date)`** — idempotent. Flips a `ready` row to
  `published` (the only status `GET /games/daily-content` will serve). If nothing is
  ready, activates a fallback package instead of leaving the day empty.
- Both are logged to `daily_content_generation_log` (success/failure, whether a
  fallback was used) — queryable via `GET /admin/content-status`.
- **Timezone: UTC**, explicitly (`app/time_utils.py`) — this was previously an
  undocumented server-local-time boundary (PRODUCTION_AUDIT.md B22); it's now a
  recorded decision, not an accident.
- **Fallback pool** (`fallback_daily_content`): a small prevalidated emergency pool,
  seeded via `POST /admin/seed-fallback-content` (hand-authored Trivia/math, a
  caller-supplied placeholder image URL — never calls OpenAI, so seeding costs
  nothing). Activation picks the least-recently-used active entry and explicitly
  avoids repeating whatever was used the prior day when another entry is available.
- All `/admin/*` endpoints require the `x-admin-token` header, compared with
  `secrets.compare_digest` (constant-time) — there is no public/unauthenticated
  generation or publication endpoint.

---

## 4. Environment variables (backend)

| Variable | Required | Notes |
|---|---|---|
| `SUPABASE_URL` | yes | |
| `SUPABASE_SECRET_KEY` | yes | Service-role key — backend only, never sent to iOS |
| `SUPABASE_ANON_KEY` | yes | Used for JWKS/auth verification server-side |
| `OPENAI_API_KEY` | yes | Backend only |
| `ADMIN_TOKEN` | yes | Shared secret for `/admin/*` — treat like a password |
| `SUPABASE_JWT_SECRET` | no | Unused by the current ES256/JWKS auth path; kept optional for compatibility |
| `ENVIRONMENT` | no (default `development`) | `development` \| `staging` \| `production` |
| `CORS_ALLOWED_ORIGINS` | no (default empty) | Comma-separated; leave empty unless a web client needs it |
| `LOG_LEVEL` | no (default `INFO`) | |

Missing any *required* var fails the process at startup with a clear pydantic
validation error — this already works today (`app/config.py`), no extra code needed.

---

## 5. Deployment steps (Render)

Two separate blueprint files so staging and production can never be deployed together
by accident: **`render.staging.yaml`** (use now) and **`render.yaml`** (production —
not yet approved, see below).

### Staging

1. Apply the pending Supabase migration (`sql/migrations.sql`'s latest block) **before**
   deploying code that depends on it, so there's never a window where deployed code
   expects columns that don't exist yet. (Already done as of 2026-08-02 — this applies
   to future migrations too.)
2. Push this branch; in the Render dashboard, "New +" → "Blueprint" → point at this
   repo → **select `render.staging.yaml`** (Render lets you pick which blueprint file
   to use) → it shows the 3 resources it will create: `blipz-backend-staging` (web,
   Free plan), `blipz-generate-tomorrow-staging` and `blipz-publish-today-staging`
   (cron jobs).
3. Fill in the `sync: false` secrets (`SUPABASE_URL`, `SUPABASE_SECRET_KEY`,
   `SUPABASE_ANON_KEY`, `OPENAI_API_KEY`, `ADMIN_TOKEN`) in the dashboard — never in
   the YAML file itself. Staging can reuse the same Supabase project as local dev
   (no separate staging Supabase project exists yet — see §2's note).
4. Approve creation — **this is the billable-resource step (though the web service's
   Free plan itself costs nothing — see the cost note below); do not do this without
   explicit go-ahead.**
5. Once deployed, run the staging smoke test (§8 below) against the new HTTPS URL.
6. Update iOS's `Staging.xcconfig` with the real staging URL (see §7) — a separate,
   explicit step; nothing here inserts a fake or guessed URL automatically.

**Staging cost:** the web service on Render's **Free** plan is **$0/mo** (cold starts
after ~15 min idle — acceptable for internal testing, not for anything time-sensitive).
The two cron jobs are lightweight (one HTTPS POST per run) and fall well within Render's
free/low-cost tier for scheduled jobs. **Expect roughly $0–1/mo for staging.**

### Production (not yet approved — do not deploy from `render.yaml` without explicit go-ahead)

Same steps, using `render.yaml` instead: `plan: starter` (~$7/mo, always-on, no cold
starts — matters for real users), `ENVIRONMENT=production`, and its own separate set of
Render secrets (can be the same Supabase project, but a distinct `ADMIN_TOKEN` is
recommended so staging and production don't share an admin secret).

## 6. Rollback procedure

- **Bad deploy**: Render keeps prior deploys — "Rollback" in the dashboard to the last
  known-good deploy. No database change is tied to a deploy by default, so this is safe
  on its own.
- **Bad migration**: use the rollback SQL block provided with that migration's handoff
  (each migration in this project ships with one) — never `DROP COLUMN` casually once
  real data may depend on it.
- **Bad published content**: `POST /admin/replace-content?content_date=YYYY-MM-DD`
  regenerates and republishes that date without waiting for the next cron cycle.

## 7. Smoke-test checklist

Run against the deployed staging URL (`https://blipz-backend-staging.onrender.com` or
whatever Render assigns) with `x-admin-token` for admin calls and a real Supabase
anonymous JWT for player-facing calls.

1. `GET /health` → `200 {"status": "ok", "environment": "staging"}`
2. `GET /health/ready` → `200 {"status": "ready", "environment": "staging"}`
3. Authenticated `GET /games/daily-content` → 200, correct shape, no forbidden fields
   (same checks as `tests/test_daily_content_security.py`) — requires content already
   published for today (step 7 below, or the historical/live data already present).
4. **Guess scoring**: `POST /games/submit-guess` with a real guess → 200 with a score;
   resubmit → `already_completed: true`, same score, no second OpenAI call.
5. **Maths submission**: `POST /games/submit-maths` with correct answers computed from
   the fetched `math_problems` → 200, `correct` matches; resubmit with wrong answers →
   still returns the original result.
6. **Trivia submission/review**: `POST /games/submit-trivia` → 200; `GET
   /games/trivia-review` → 200 with matching selected/correct answer text.
7. **Generate-ahead**: `POST /admin/generate-content` (defaults to tomorrow, UTC) →
   `{"status": "ready"}`.
8. **Duplicate generate**: repeat step 7 → same `{"status": "ready"}`, and confirm via
   `/admin/content-status` that `recent_generation_log` did not grow (no new OpenAI
   spend on the duplicate call).
9. **Publish**: `POST /admin/publish-content` (defaults to today, UTC) →
   `{"status": "published"}`.
10. **Duplicate publish**: repeat step 9 → `{"message": "Already published", "status":
    "published"}`.
11. **Fallback**: pick a date with nothing generated (e.g. 3 days out),
    `POST /admin/publish-content?content_date=YYYY-MM-DD` → `{"status": "published",
    "used_fallback": true}` — requires the fallback pool to be seeded first
    (`POST /admin/seed-fallback-content`, one-time).
12. **`/admin/content-status`**: confirm `today`/`tomorrow` show the expected
    status and the generation log reflects steps 7–11 accurately.
13. **Restart behavior**: restart the Render service (dashboard "Manual Deploy" →
    "Restart", or just wait for a routine redeploy) → confirm `GET /health` recovers
    within Render's health-check grace period, and that startup logs show
    `"In-process scheduler NOT started — relying on external cron"` (never started in
    staging/production, so a restart can't accidentally duplicate a scheduled job).
14. Point a Staging-configured iOS build at the URL; play all three games end to end.

**Production-only additions** (once production is approved and deployed):

15. Confirm `CORS_ALLOWED_ORIGINS` is empty (or exactly the intended list) — check
    response headers from a browser devtools request, not just config.
16. Confirm the two Render Cron Jobs both show a successful run in the dashboard after
    their first scheduled fire.
17. Confirm `ENVIRONMENT=production` in the running service's logs at startup.

---

## 8. iOS: one-time Xcode wiring for the environment-aware config

`Blipz/Configs/{Shared,Debug,Staging,Release}.xcconfig` and the new `Config.swift`
already exist in the repo (see `blipz-ios` commit `6d61741`) and are visible in Xcode's
navigator, but attaching an `.xcconfig` to a build configuration — and adding the new
"Staging" configuration itself — is a project-settings change in Xcode's own project
model. That was deliberately **not** done by hand-editing `project.pbxproj`: that file
has no safe text-based way to add a build configuration, and a bad edit risks
corrupting the whole project with no easy way to detect it short of opening Xcode.
These are standard, low-risk GUI steps instead:

1. Select the **Blipz** project (top of the navigator) → the **Blipz** project (not
   target) → **Info** tab → **Configurations**.
2. Next to **Debug**, expand it and set the **Blipz** target's configuration file to
   `Configs/Debug.xcconfig` (the dropdown will list it automatically).
3. Do the same for **Release** → `Configs/Release.xcconfig`.
4. Click **+** under Configurations → **Duplicate "Release" Configuration** → name it
   **Staging** → set the **Blipz** target's configuration file to
   `Configs/Staging.xcconfig`.
5. (Optional, for one-click Staging builds) **Product → Scheme → Manage Schemes** →
   select **Blipz** → **Duplicate** → rename to **Blipz Staging** → **Edit Scheme** →
   for **Run** and **Archive**, set **Build Configuration** to **Staging**.
6. Build once for each configuration (Debug, Staging, Release) to confirm nothing
   broke — `Config.swift` will `fatalError` at **launch** (not build time) if a
   non-Debug configuration is missing `APIBaseURL` or still has the placeholder value,
   so a quick run (not just a build) of the Staging/Release scheme is worth doing once
   you have a real URL to put in those files.

After deploying (§5), replace the placeholder line in `Staging.xcconfig` and/or
`Release.xcconfig` with the real Render URL and commit that change — that's the entire
remaining step once hosting exists.
