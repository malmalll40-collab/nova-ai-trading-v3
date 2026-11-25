# NOVA v5.6.7 KIS minute coverage

- Same endpoint and original strict required-minute, opening/closing volume, price and duplicate checks. No synthetic rows, no alternate price source, no observation overwrite.
- Pagination cap 6 -> 12, plus at most 4 targeted missing-minute queries per reference request. The existing collector may retry once; actual provider limits and latency require verification.
- Record required/received counts, precise missing timestamps, stop reason, actual zero-volume rows and structured failure codes, with sanitized logs. Existing research strategy version and DB schema remain unchanged.
- Local: 45 targeted new and regression tests passed. Provider-dependent recovery not yet verified; 18 prior records must be preserved.
