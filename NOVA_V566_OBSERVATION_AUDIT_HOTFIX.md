# NOVA prospective audit hotfix

2026-11-25 run attempt 2: 18 SAVED, 2 UNVERIFIED, no exceptions; prior logs lacked symbol identifiers. Adds read-only database count and day/preset reconciliation at startup and collector completion, and safe per-symbol reasons. Existing immutable records and validation gates remain unchanged. Same-day rerun uses existing skip logic. Five focused local SQLite and diagnostic tests passed; production PostgreSQL and server integration still require verification.
