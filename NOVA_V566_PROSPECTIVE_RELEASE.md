# NOVA v5.6.6 prospective collection
- Candidate deployment: two weekday GitHub Actions runs at 18:15 and 20:15 KST; execution can be delayed or missed. Same-day exchange calendar and verified daily observation gates remain mandatory. Missing days are never backfilled. The second run skips existing immutable records and attempts unrecorded symbols.
- Uses the existing core561 research observation table and frozen 15 bp per-side cost. Price-only records are not counted as verified signals, partial remains partial, no live adoption or orders.
- Independent opt-in: Render NOVA_CORE566_AUTO_ENABLED=1 and matching existing NOVA_AUTOTRACK_TOKEN secret in Render and GitHub Actions; existing NOVA_CORE541_EVIDENCE_ENABLED and persistent DB required. Do not enable legacy auto endpoints. Never put a token in source. The schedule remains unverified until an actual run is observed.
- Read-only checkpoint probe reports table presence/count at startup, not proof of successful resume. Actual v5.6.5 checkpoint store/restart remains to be tested through authenticated Research Lab.
- Five local unit tests cover scheduling gate, resume, price-only, cross-day and failure behavior. Full regression and production database collection not yet verified.

- 2026-11-25 local targeted regression/integration: 46 tests passed (including SQLite actual insert, duplicate prevention, and endpoint authentication). Not a full-suite or production Neon test. Status UI points at core566, and startup logs only scheduler flag/token-presence booleans.
