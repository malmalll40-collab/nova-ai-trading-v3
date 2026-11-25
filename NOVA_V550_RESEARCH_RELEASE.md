# NOVA v5.5.0 · 5-Core regime research & prospective evidence

## Changes (research only)
- Retains the v5.4.4 chart display/settlement guard and existing live decisions, recommendation ranking, tokens and KIS/Neon settings. No orders.
- Three fixed candidates: range breakout guard, early upward transition, regime switch. Existing risk veto; no new buy in stock/index down regime or excessive index volatility. Range requires volume breakout, improving ADX, positive relative strength. Early transition additionally requires EMA20 slope crossing zero and close near/above EMA50. Thresholds are unoptimized hypotheses, not proven improvements.
- Individual and common-period multi-stock tests include new candidates, double-cost stress, and added/blocked positive/negative five-session opportunities. Regime subsets are separate simulations, not additive. Stock-day counts are not independent trades.
- A conditional frequency probability candidate groups by stock regime and existing Core signal. Minimum 40 earlier settled outcomes, 10 in group, shrinkage weight 30 toward smoothed base rate. Historical replay only learns labels ending strictly before signal date. Paired Brier against smoothed naive baseline, not a claim of calibrated probabilities or superiority.
- Version-separated `nova_core550_observations` table. Uses existing `DATABASE_URL` (or explicit persistent SQLite) and `NOVA_CORE541_EVIDENCE_ENABLED=1`. First same-day, verified KIS regular-session swing observation after 18:00 KST wins. No backfill; no automatic market-wide collection. Previous tables preserved and previous Hybrid report still accessible. Each prospective forecast (if enough prior same-cost outcomes) is frozen on insert; entries and exits require actually observed verified dates. Missing records remain missing. Report uses up to 1000 recent verified observations per symbol; overlapping opportunity outcomes are not trade win rates. No automatic adoption.

## Local checks
- Python: 561 tests passed (548 prior, including version expectation updates, +13 new). JavaScript chart controller test and modified JS syntax passed. Synthetic fixtures/local tests do not prove investment performance.
- Production Render deployment, actual KIS/Neon data, mobile browser and future performance not verified. Historical regular-session scope, adjusted prices, execution and selection bias limitations remain.
- Existing API research version field `5.4.3` is kept for compatibility; overall application `/healthz` is `5.5.0` and new UI is marked v5.5.0.
