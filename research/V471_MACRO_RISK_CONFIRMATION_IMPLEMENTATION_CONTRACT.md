# v4.7 Macro-Risk Confirmation Implementation Contract

The implementation must satisfy all items below.

1. Reuse the frozen v4.3 model family and v4.4 cash-yield accounting.
2. Do not change crypto feature semantics, labels, sealed windows, costs or acceptance gates.
3. Download `VIXCLS`, `DTWEXBGS` and `DFII10` from fixed FRED CSV URLs covering 2020-01-01 through 2026-06-30.
4. Record raw SHA-256, observation count and source date range for every macro series.
5. Parse missing FRED observations as unavailable; never forward-fill past seven calendar days.
6. For decision date `D`, use only macro observations dated `<= D-1`.
7. Calculate the three protocol-defined trailing percentile components without future observations.
8. Keep macro state changes on the existing rebalance cadence; never create an off-cadence action.
9. Never change selected asset identities relative to the frozen base decision for the same rebalance date.
10. Keep per-asset target between 0% and 7.5% and aggregate target exposure at or below 15%.
11. Run six independently refitted walk-forward base models before sealed evaluation.
12. Apply the protocol eligibility rules to both standard and stress costs.
13. Fall back to the exact v4.4 baseline when no active configuration is eligible.
14. Report fold-level control and macro summaries, selected configuration, macro-state counts, target multipliers, action count, turnover and exposure.
15. Reproduce the frozen v4.3 source inventory, dataset metadata, bundle summary and sealed evaluation exactly before v4.7 evaluation.
16. Emit `paper_only=true`, `authorizes_trading=false`, `retrospective=true` and `untouched_historical_dates=false`.
17. Include focused tests for parsing, one-day lag, stale-data failure, percentile bounds, exposure bounds, selection fallback and asset-identity preservation.
18. Archive the v4.3 report, bundle and v4.7 JSON evidence in GitHub Actions.
