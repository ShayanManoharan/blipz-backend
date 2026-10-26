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


## Atomic gameplay migration

Apply order in the disposable harness: `20261026_v2_schema.sql`, then
`20261026_v2_gameplay.sql`. This is not approval to apply either to Supabase.
The gameplay migration adds three service-role-only SECURITY DEFINER functions:
`start_game_attempt`, `reveal_game_choices`, and `submit_game_answer` in
`blipz_v2`. Every function pins `search_path=pg_catalog` and uses fully qualified
application relations. Private helper functions have no caller EXECUTE grants.
The second migration also closes SQL CHECK's UNKNOWN/null answer-kind edge case
with an explicit constraint on the new V2 attempts table.

All mutations lock user -> daily result -> attempt. The user's lock serializes
simultaneous games, first-activity insertion and streak advancement. Actual clock
reads occur after lock waits; elapsed milliseconds are database wall-clock time,
not active-thinking time or an anti-cheat guarantee. Deadline checks are inclusive
at opening and exclusive at UTC closing. Completed retries bypass deadline checks
and return the original final attempt. A completed attempt is never reset.

Typed inputs cross a TRUSTED BACKEND boundary: `app/v2/rpc_inputs.py` derives
`p_normalized_text` from a validated raw answer using existing normalization v1.
That argument must never come from client JSON. Approved aliases are normalized by
the same trusted Python code before immutable solution insertion. PostgreSQL 17
lower() is not Unicode casefold(), so SQL deliberately does not approximate it.
The RPC compares the normalized value with immutable normalized aliases and
computes correctness/points itself; no score or client elapsed time is accepted.
SQL permissions restrict this interface to service_role, and typed submissions
without backend normalization are rejected. Raw answer text remains unchanged in
the attempt. HTTP authentication and wiring remain future work.

The fixture loader's approval flags are explicitly synthetic database fixtures;
they are not editorial approval or production publication. Tests may shift their
own challenge date by one day to exercise closed/future windows. No server clock
is changed and no real published content is used.

Concurrency tests use independent psql sessions, hold the user row lock, and
verify both contenders are actually waiting on PostgreSQL locks before release.
The suite covers reveal/submit, duplicate typed/choice submissions, two starts,
simultaneous game completions, zero-point activity, timer preservation, exact UTC
closing, terminal retry after midnight, safe search paths and deferred failure
rollback of attempt + result + activity + user streak. UTC-day conflict checking
covers all users' V1 completions; it does not replace the future HTTP cutover gate
because the unchanged V1 RPC cannot consult V2 state.

Verified local counts for this phase: 27 schema tests and 37 gameplay tests,
64 total. V2 pure tests: 241 passed. Existing offline V1 subset: 113 passed,
1 database-dependent skip. Documentation fixture checks: 22 passed. Shared/live
Supabase integration tests remain intentionally unrun.
