# Blipz V2 contract review — local proposal only

This directory is documentation and local example data, not application implementation or a database migration. Nothing here is approved production content. No endpoint, RPC, screen, deployment, or database change is implemented.

- `CONTRACTS.md`: exact endpoint envelopes, lifecycle, scoring, ranking, compatibility, and pending decisions.
- `contract.schema.json`: JSON Schema 2020-12 definitions for all 19 requested domain contracts, plus their nested and wire-response types. Every declared object rejects unknown properties; all listed properties are required, with explicit nulls where permitted.
- `fixtures/content.json`: one Hidden Rule, one Riddle, one five-question Trivia set, one flag World Guess, and one outline World Guess. Internal revisions and player-safe projections are explicitly separate.
- `fixtures/gameplay.json`: local synthetic attempts, a completed 88.75/100 day, activity, protected review, rankings, history, and percentile arithmetic examples.
- `fixtures/api-examples.json`: exact local request/response examples for every proposed endpoint plus a locked-completion error; independent illustrative snapshots, not real HTTP captures.
- `fixtures/state-transitions.json`: legal and rejected transition/concurrency examples, not observations of running backend behavior.
- `fixtures/assets/`: two clue SVGs with neutral filenames and alt text. They are documentation assets, not added to iOS.
- `fixtures/asset-provenance.json`: exact Natural Earth source geometry and source-download hash for the outline.
- `check_fixtures.py`: offline schema-subset and semantic checks, including adversarial fixture mutations. Uses only Python's standard library; no application imports, network, environment secrets, or database access.

Run from this directory:

```sh
python3 -B check_fixtures.py
```

The checker is purpose-built for the JSON Schema keywords emitted here; it is not a replacement for a complete JSON Schema implementation or future backend tests. Assertions about transaction serialization describe the proposed contract, not a tested PostgreSQL RPC.

All IDs, users, dates, lifecycle states, and rankings are synthetic. The example challenge uses `published` to demonstrate the wire shape, but the enclosing fixture explicitly sets `publication_eligible=false`, and every content revision remains `human_approval=not_reviewed`. No publication is authorized or possible from these files alone.

Local fixture URI resolution: `local-fixture://assets/<name>` maps to `fixtures/assets/<name>` in this directory. Real player-facing asset URIs must use approved immutable HTTPS storage; the fixture scheme must be rejected outside offline examples.

The flag is a locally composed depiction of Japan's flag. The mainland Portugal outline is derived from public-domain Natural Earth 110m geometry; its source and transformation are recorded. Neither is claimed to be production editorially approved. Neutral alt text avoids revealing the answer; a usable nonvisual alternative remains a product/accessibility decision before launch.
