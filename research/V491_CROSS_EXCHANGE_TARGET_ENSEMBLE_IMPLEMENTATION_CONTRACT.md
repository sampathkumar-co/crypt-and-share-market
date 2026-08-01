# v4.9 Cross-Exchange Target Ensemble Implementation Contract

1. Reuse the v4.8 Coinbase source loader and combined 39-feature construction without changing feature semantics.
2. Reproduce the frozen v4.3 source inventory, base dataset, bundle summary and sealed evaluation before v4.9 comparison.
3. Preserve the original labels, costs, cadence, exposure, long-or-cash constraint, sealed windows and profitability gates.
4. Independently fit/calibrate a control and combined-feature bundle in each of the six walk-forward folds.
5. Evaluate exactly three active combined-model weights: 0.25, 0.50 and 0.75, plus the disabled baseline.
6. Construct portfolio targets as the weighted sum of the two models' target portfolios; do not blend future returns or reported metrics after simulation.
7. Net the two sleeves before calculating transaction turnover and costs.
8. Preserve each model's existing panic semantics and the portfolio's existing three-day rebalance cadence.
9. Keep aggregate target exposure at or below 10% and each sleeve long-or-cash.
10. Apply prior-day-known DGS3MO yield only to actual shared idle cash after rebalancing and costs.
11. Apply every protocol eligibility rule under both standard and stress costs before sealed evaluation.
12. Fall back to exact v4.4 when no active weight is eligible.
13. If active, independently fit/calibrate one final combined-feature bundle before sealed evaluation; reuse the frozen final control bundle.
14. Report per-fold control/combined/ensemble summaries, model decisions, disagreement counts, actions, turnover, exposure, selected weight and eligibility reasons.
15. Report final standard/stress windows, gates, source hashes, bundle summaries and v4.4 comparison.
16. Emit `paper_only=true`, `authorizes_trading=false`, `retrospective=true`, and `untouched_historical_dates=false`.
17. Add focused tests for target blending, same-asset conservation, different-asset splitting, panic sleeve cashing, exposure bounds, net turnover, eligibility, fallback and disabled exactness.
18. Archive the v4.3 report/bundle and v4.9 JSON evidence through GitHub Actions.
