# NOVA v5.5.2 — Analysis Live & Regime Research

Base: NOVA v5.5.1. No orders. All additional research strategies are shadow-only. Existing daily settlement guard, 13-role decisions, financial gate, KIS authentication and Neon settings are retained.

## Chart display
- Analysis-period chart preserves a cloned, immutable completed analysis series and appends only newer, chart-only KIS bars. These are distinguished by a gold dashed outline and excluded from historical signal markers, analysis, backtesting and evidence recording.
- Swing mode uses the working KIS native daily snapshot path. The minute modes use same-day regular-session KIS 1-minute OHLCV, aggregated into 1/5/15-minute buckets. No invented or forward-filled bars. Missing constituents remain provisional. The client polls every 20 seconds when active and backs off on failures. Known holidays are blocked; a bounded KIS probe is allowed when the exchange calendar is unknown.
- A display-only bar is not the same as a completed/validated analysis bar. The last analysis bar remains the date of the frozen analysis result; separate reanalysis is needed to incorporate later completed bars. This is not a tick/WebSocket feed; actual broker latency and rate limits remain unverified.
- Existing 1d/W/M/Y chart handling remains. The new read-only authenticated route is `/api/chart/analysis-live`.

## Strategy audit and frozen candidates
- Compare all 13 strategy/candidate signal vectors on aligned dates. Report joint BUY, exclusive BUY for each, joint HOLD, BUY union, Jaccard and overall agreement. All-HOLD pairs are not declared duplicates. Identical observed vectors or high BUY overlap are not logical equivalence. Single-symbol and common multi-symbol reports expose the audit.
- v5.5.0 range_guard and regime_switch share the trend/range branches. The early-turn branch can differ: ADX 18–20 and a slope reversal can qualify early_turn without range_guard. Conversely already-positive slope can qualify range_guard without early_turn. Synthetic counterexamples are tested; actual 20-stock signal vectors require the user's KIS historical run.
- `rs_hybrid`: intersection (AND) of relative strength required and relaxed Hybrid transition. It is necessarily a subset of each constituent's buys, and is not intended to increase recall on its own.
- `regime_router`: strong up → core, mild up → rs_hybrid, sideways → rs_required, upward transition → rs_hybrid, down or veto or unknown → HOLD. Fixed hypothesis, not a trained classifier or an automated winner selection. Stock and prior-index regime labels are computed causally. This does not change the real signal engine.
- Existing four base strategies and seven candidates preserved; two added (13 total). All historical performance is exploratory, including common-period replay and removal experiments. No claim of improved returns.

## Prospective validation
- New version-specific immutable `nova_core552_observations` ledger, no retrospective record creation. Previous v5.5.0 and v5.4.2 report routes stay available; the old v5.5.0 writer explicitly keeps only its original keys.
- Existing `NOVA_CORE541_EVIDENCE_ENABLED=1` and durable DB required. Use cost 15 bp per side. Analyze a fixed chosen symbol in swing mode after 18:00 KST on each trading day. Only same-day regular-minute-verified bars can be recorded. First same-day record is immutable; unavailable signal records are counted separately.
- Next verified trading-day opening price and fifth trading-day closing price, both observed prospectively, determine net outcome. Missing verified prices are reported; no backfill. Compare all 13 strategies, regime-level outcomes, overlap, missed positive/blocked losses, and a non-overlapping-window view. Brier uses only previously matured observations and conditional shrinkage; compare on paired samples. A minimum 40 nonoverlapping windows and 40 paired probabilities are screening thresholds only, not proof of superiority; no automatic adoption.
- No automatic collection for the 20-symbol cohort in this release. Actual independent future observations are not available at packaging time. Market calendar failures continue to block production signals and prospective records rather than falsely approve them. Render/KIS/Neon and physical mobile browser operation require deployment verification.
