# NOVA collector integrity retry hotfix

2026-11-25 attempt 3: 18 existing observations independently confirmed, 2 UNVERIFIED (086520.KQ, 263750.KQ), both DAILY_INTEGRITY_HOLD. No new records or duplicates. Adds allowlisted integrity state and KIS error codes, and one bounded collector-only requery for same-day KIS minute query/coverage errors after clearing only the target negative cache entry. Does not relax verification, manufacture candles or overwrite observations. 42 targeted local tests passed; production recovery and exact provider-level cause not yet verified.
