# Offline verification

Run from the backend root using its existing virtual environment:

```sh
venv/bin/python -B -m pytest -p no:cacheprovider tests_offline/v2
venv/bin/python -B tests_offline/run_v1_regressions.py
python3 -B docs/v2-contracts/check_fixtures.py
```

V2 tests block network access. The V1 runner executes seven existing test files
unchanged, disables their live parent conftest, uses synthetic configuration, and
blocks sockets/DNS before importing application modules. Its migration-gated
scoring integration test skips without database access. This is an offline V1
regression subset, NOT the full database-backed backend suite.

The complete legacy suite includes real Supabase reads/writes during collection
and fixtures, and must not be run under a no-Supabase authorization. No live
integration result is implied by the offline checks.
