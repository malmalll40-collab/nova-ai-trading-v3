# NOVA v5.6.4 research reliability

- Retry transient unavailable/exception once after two seconds; keep KIS/source and cost assumptions, never promote insufficient data. Failed cache entries are invalidated, successful concurrent results are preserved.
- Share matured, nonoverlapping historical pools per date and candidate, then filter symbol/sector for holdout. Same historical exit-date gate, sizing, risk veto, and probability math.
- Local 43 targeted regression tests passed. Synthetic comparison versus v5.6.3, 20 stocks x 200 dates: 4,000 identical decision records per mode; normal 2.85x and holdout 4.54x on that local workload. These are not measured production speed or prediction accuracy.
- Full regression suite not confirmed (execution time limit). Existing release SHA256 manifest describes the original v5.6.3 archive only.
- No database schema or credential changes, no automatic live strategy adoption, no orders. Persistent batch checkpoints and verified prospective collection remain separate work.
- Startup full diagnostics remain disabled; initiate a research rerun manually after verifying deployment.
