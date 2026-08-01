# v4.8 Cross-Exchange Confirmation Implementation Contract

1. Reproduce the frozen v4.3 source inventory, dataset metadata, bundle summary and sealed evaluation before comparing v4.8.
2. Preserve all original v4.3 feature columns, labels, regimes, costs, cadence, exposure and acceptance gates.
3. Download Coinbase `BTC-USD` and `ETH-USD` daily candles from 2021-01-01 through 2026-06-30 using requests of at most 250 days.
4. Archive every request URL, requested interval, raw SHA-256 and parsed row count.
5. Require complete, unique, valid UTC daily candles for both products across the fixed interval.
6. Build date-`D` cross-exchange features only from Coinbase and Binance observations dated `<= D`.
7. Prove by test that changing Coinbase observations after date `D` does not change date-`D` features.
8. Provide exactly three active feature families: `price`, `liquidity`, and `combined`, plus the disabled baseline.
9. Repeat market-context features identically across all five asset rows on a date; do not encode future asset identity or labels.
10. Use the existing v4.3 model and calibration grids unchanged for every control and augmented fit.
11. Independently refit and calibrate the control and all active families in every one of the six walk-forward folds.
12. Apply the protocol eligibility and tie-breaking rules without sealed-window information.
13. Fall back to exact v4.4 when no active family is eligible.
14. If active, train one final augmented bundle using only the frozen v4.3 training and calibration dates before sealed evaluation.
15. Apply v4.4 prior-day-known DGS3MO cash yield in all validation and sealed simulations.
16. Keep maximum target exposure at or below 10%, long-or-cash only, and next-bar-open execution.
17. Report source metadata, augmented feature names/hash, fold controls, fold candidates, excess returns, selected family, final calibration, sealed metrics, gate values and v4.4 comparison.
18. Emit `paper_only=true`, `authorizes_trading=false`, `retrospective=true`, and `untouched_historical_dates=false`.
19. Add focused tests for request ranges, parsing/completeness, feature causality, family composition, disabled fallback, eligibility, augmented shape and exposure preservation.
20. Archive the v4.3 report/bundle and v4.8 JSON evidence in GitHub Actions.
