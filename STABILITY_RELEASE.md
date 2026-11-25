# NOVA v4.5 — Stability maintenance (2026-11-12)

* Preserves the production read-only 13-role analysis, strict signal policy, high-precision research, investor-flow fallback, and structural ATR risk overlays.
* Prevents unknown bare 6-digit numbers from being silently classified as KOSPI. Name search resolves the market, known fallback names remain accessible, unknown direct API inputs require explicit .KS/.KQ.
* Coalesces simultaneous upstream requests for the same cache key with a bounded striped lock.
* 429 responses now specify a retry delay and mobile quote polling observes a local 60s cooldown.
* Mobile quote and intraday requests can be aborted per request on switching symbols; stale flow promises cannot start old-symbol market polling. The flow table clears before starting the next request.
* Restores intraday refresh on returning to recommendations view.
* Adds safe status indicators for KIS configured/not configured; credentials themselves are never exposed.
* Bumps PWA service-worker cache identifier so users receive new JavaScript. Trading API responses remain network-only.

**Important:** This does not fix missing KIS credentials. On Render, KIS_APP_KEY/KIS_APP_SECRET must be configured to enable KIS quote/minute candles. Never share the credential values. It also does not claim verified trading performance or real-time Yahoo/Naver quote data.

**Deployment:** Replace exactly one NOVA_v4_5_update.zip file in the GitHub repository, then confirm Render is live at the newly committed SHA. Take a backup first. No automated order changes.


- Regression and deployment detail: see NOVA_v45_안정화_검증보고서.md inside ZIP.


- Regression and deployment detail: see NOVA_v45_안정화_검증보고서.md inside ZIP.


- Regression and deployment detail: see NOVA_v45_안정화_검증보고서.md inside ZIP.
