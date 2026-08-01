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

1. Apply the pending Supabase migration (`sql/migrations.sql`'s latest block — see the
   handoff format used in prior migrations of this project) **before** deploying code
   that depends on it, so there's never a window where deployed code expects columns
   that don't exist yet.
2. Push this branch; in the Render dashboard, "New +" → "Blueprint" → point at this
   repo → Render reads `render.yaml` and shows the 3 resources it will create (1 web
   service, 2 cron jobs).
3. Fill in the `sync: false` secrets (`SUPABASE_URL`, `SUPABASE_SECRET_KEY`,
   `SUPABASE_ANON_KEY`, `OPENAI_API_KEY`, `ADMIN_TOKEN`) in the dashboard — never in
   `render.yaml` itself.
4. Approve creation — **this is the billable-resource step; do not do this without
   explicit go-ahead.**
5. Once deployed, run the staging smoke test (below) against the new HTTPS URL before
   pointing any real iOS build at it.
6. Update iOS's Staging/Release xcconfig with the real hosted URL (see §7) — a
   separate, explicit step; nothing here inserts a fake or guessed URL automatically.

## Rollback procedure

- **Bad deploy**: Render keeps prior deploys — "Rollback" in the dashboard to the last
  known-good deploy. No database change is tied to a deploy by default, so this is safe
  on its own.
- **Bad migration**: use the rollback SQL block provided with that migration's handoff
  (each migration in this project ships with one) — never `DROP COLUMN` casually once
  real data may depend on it.
- **Bad published content**: `POST /admin/replace-content?content_date=YYYY-MM-DD`
  regenerates and republishes that date without waiting for the next cron cycle.

## Staging smoke test

1. `GET /health` → `200 {"status": "ok"}`
2. `GET /health/ready` → `200 {"status": "ready"}`
3. `POST /admin/generate-content` with a valid `x-admin-token` → `ready`
4. `POST /admin/publish-content` → `published`
5. Authenticated `GET /games/daily-content` (real Supabase anon JWT) → 200, correct
   shape, no forbidden fields (same checks as `tests/test_daily_content_security.py`)
6. Point a Staging-configured iOS build at the URL; play all three games end to end.

## Production smoke test

Same as staging, plus:
7. Confirm `CORS_ALLOWED_ORIGINS` is empty (or exactly the intended list) — check
   response headers from a browser devtools request, not just config.
8. Confirm the two Render Cron Jobs both show a successful run in the dashboard after
   their first scheduled fire.
9. Confirm `ENVIRONMENT=production` in the running service's logs at startup.
