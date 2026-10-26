# Disposable V2 PostgreSQL verification

These tests never read `.env`, app settings, a database URL, or Supabase credentials.
They create their own randomly named PostgreSQL 17.11 Docker container, with
`--network none`, no published ports, and RAM-backed database storage. Connections
use `docker exec psql` and the container's Unix socket. No external DSN is accepted.
The exact official image digest is pinned in `v2/local_db.py`.

```sh
BLIPZ_V2_ISOLATED_DB_TESTS=1 venv/bin/python -B -m pytest -p no:cacheprovider tests_database/v2
```

Opt-in and Docker access are required. Missing Docker/image/database setup fails
the opted-in run; it is not reported as a database-test success. Fixtures remove
their own containers and volumes in finally blocks. No container restart policy,
host ports, or persistent host database volumes are used.

The local prerequisite schema replays checked-in V1 migrations and supplies minimal
Supabase role/auth-trigger prerequisites. The historical “Fix schema drift” repair
block is omitted ONLY in the local bootstrap because the initial CREATE TABLE
statements already declare those same foreign keys. All other V1 migration SQL is
used unchanged. This is the repository-expected V1 baseline, not an assertion about
the uninspected live catalogue or hosted PostgreSQL version.

Schema tests compare V1 public columns, constraints, function definitions, and
synthetic historical baseline rows before/after V2 migration. Isolated test users
and content are clearly synthetic and never leave the disposable database.

Storage is under `blipz_v2`, with solutions in `blipz_v2_private`. All tables have
RLS enabled with no client policies. PUBLIC/anon/authenticated have no schema,
table, or function access. service_role can read nonprivate V2 tables but cannot
directly mutate attempts/results/activity or read private solutions. Only explicit
gameplay RPCs in the later migration receive EXECUTE grants. Owner/admin fixture
inserts are restricted to the disposable environment in these tests.

Content and private solution records are insert-once: editing/approval creates a
new immutable revision rather than overwriting identity. A published lineup is
sealed. Deferred constraints verify complete approved lineups and authoritative
result aggregates at transaction commit. `fallback_packages` is a minimal FK
scaffold only; no fallback/generation/publication system is implemented.

This phase does not expose schemas through PostgREST, register HTTP routes, change
the existing V1 completion RPC, or enforce the future HTTP V1/V2 cutover gate.
