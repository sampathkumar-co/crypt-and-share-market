# V4.4.1 Yield-Bearing Cash Implementation Contract

## Scope

Implement a minimal accounting overlay on the frozen v4.3 distributional-utility strategy.

## Required invariants

1. Import and reuse the v4.3 model, dates, bundle, decisions and thresholds.
2. Do not alter candidate ranking, regime selection, risk gates, exposure, cadence or costs.
3. Apply cash yield only to the currently idle cash balance.
4. Use only a Treasury observation known by the prior UTC day.
5. Fail closed if the cash series does not cover the dataset or if an applicable prior observation is unavailable.
6. Record the cash source URL, raw SHA-256, count and date coverage.
7. Record Python, NumPy, scikit-learn and joblib versions.
8. Compare every sealed window against v4.3 and assert unchanged actions and selections in the report.
9. Label the result retrospective and set `untouched_historical_dates` to false.
10. Keep `paper_only=true` and `authorizes_trading=false`.

## Acceptance checks

- Cash parser accepts FRED `observation_date,DGS3MO` CSV and skips `.` values.
- Same-day cash observations are not usable.
- Annual-to-daily compounding is mathematically reversible within floating-point tolerance.
- An all-cash simulation earns cash yield without generating a trade.
- A selected-asset simulation preserves the v4.3 action count and selected assets.
- The fixed FRED query begins no later than 2022-01-01 and ends on 2026-06-30.
- Existing v4.3 tests continue to pass.

## Prohibited changes

- No gate weakening.
- No transaction-cost reduction.
- No date removal or window reshaping.
- No same-day or forward-filled future information.
- No live exchange keys, order placement or trading authorization.
