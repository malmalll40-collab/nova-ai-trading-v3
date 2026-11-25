# NOVA v5.6.3 — DB diagnostics and research progress

Base: v5.6.2 Startup Hotfix. Research model and live decisions unchanged.

- PostgreSQL probe uses the same connect arguments as existing persistence (`connect_timeout=5`), not a startup `options` parameter. Sets a read-only transaction and `SET LOCAL statement_timeout = 5000`. No table creation or data writes. Reports `CONNECT_FAILED` or `READ_FAILED`, stage, allowlisted exception type and validated SQLSTATE; never exception text or credentials.
- Research progress records `batch_start`, `symbol_start`, `symbol_done`, `aggregate_start`, `aggregate_done` with count and elapsed time. Failed symbols continue as before. This is event-based, not a heartbeat or hard timeout; a stuck worker is not automatically cancelled.
- Authenticated operations status displays the matching batch progress; final event includes elapsed time and failed stage. No actual order or strategy adoption.
- Existing prospective research version v5.6.1/table remains unchanged. The app version only is 5.6.3.

Verification: 131 selected Python regression tests passed (including 7 new cases), five existing JS suites and syntax check passed, local Uvicorn startup and GET /healthz and / returned 200. PostgreSQL branch tested with mocked driver, not an actual Neon connection. Local FastAPI 0.128.2, Python 3.13.5; actual Render versions differ. Full Python suite not run. Real operational research and DB result not yet verified.
