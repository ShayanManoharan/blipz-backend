# Pure V2 domain foundation

This package implements approved local domain shapes, scoring, normalization,
initial divisibility-rule validation, structural content checks and ranking keys.
It does not implement routes, attempt transactions, persistence, generation,
publication, streak mutation, or leaderboard queries. Importing it does not load
V1 settings, secrets, Supabase, OpenAI, or an HTTP client.

- `models.py`: strict, extra-forbidden typed domain and request/response models,
  with conditional telemetry/result invariants. Shapes correspond to
  `docs/v2-contracts/contract.schema.json`.
- `answers.py`: exact normalization v1 and approved-alias matching only.
- `scoring.py`: integer hundredths; no duration input or speed bonus.
- `rules.py`: bounded `integer_divisible_by` predicates and finite-domain MCQ
  equivalence checks. The safety budget is at most 10,000 integers per domain.
- `validation.py`: pure per-game/cross-field checks, revision identity hashing,
  explicit public projection, and a pure publication-eligibility gate. None
  publishes, fetches, approves, generates or stores anything.
- `ranking.py`: completed-only deterministic/performance keys and full-cohort
  percentile arithmetic. Future database queries must establish eligibility and
  full population; a pure helper cannot establish that a caller supplied all users.

All launch aliases are approved strings; nonempty `approved_patterns` is rejected.
No eval, arbitrary executable content, semantic judge, fuzzy matching, or regex
execution exists. Structural checks do not establish factual correctness, global
rule uniqueness, editorial approval authenticity, asset reachability, or accessible
gameplay. Local World Guess assets parse only as explicitly unreviewed examples;
publication eligibility rejects the local scheme and unapproved content.

Models validate values supplied in memory. They cannot authenticate server
timestamps, enforce irreversible persistence, serialize requests, derive final
answer correctness from a locked database revision, or advance streaks. Those
guarantees require the future reviewed RPC/application layer. Frozen models are
not deep-immutable storage: never mutate nested lists or bypass validation with
`model_construct`/unchecked `model_copy` at a trust boundary; parse fresh inputs.

The final approved timing policy is wall-clock elapsed time (including background,
deliberation and network delay), not active-thinking measurement or an anti-cheat
guarantee. UTC deadline is exclusive and unfinished attempts expire. V1/V2 cannot
both accept competitive submissions for one UTC date. No cutover code is added.

Offline tests intentionally live outside `tests/`, whose existing conftest queries
Supabase during collection. From the backend root:

```sh
venv/bin/python -B -m pytest -p no:cacheprovider tests_offline/v2
python3 -B docs/v2-contracts/check_fixtures.py
```

Tests block network operations and verify a clean-process import cannot pull in
V1 configuration or external clients. No existing V1 application file is changed.


`rpc_inputs.py` is a pure service-side argument adapter for the separately reviewed
V2 SQL RPCs. It preserves raw answers and derives normalized typed text; clients
must never supply that internal normalized argument. It makes no database calls
and registers no route. The SQL migration and disposable database tests live in
`sql/20261026_v2_gameplay.sql` and `tests_database/` respectively.
