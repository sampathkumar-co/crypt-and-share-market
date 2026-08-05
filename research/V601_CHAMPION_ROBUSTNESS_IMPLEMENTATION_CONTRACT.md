# v6.0.1 Champion Robustness Implementation Contract

Status: frozen before delayed-execution and decision-interval concentration outcomes.

## Scope

This contract adds diagnostics to the unchanged corrected v3.1.2/v3.2 champion. It does not change the model, assets, exposure, cash yield, costs, cadence or original reports.

## Sources

The diagnostic must run independently on:

- Binance public archive daily opens using the v3.1.2 source path;
- Coinbase Exchange public daily candles using the v3.2 source path;
- the same prior-day-known DGS3MO cash-return history.

Both current authoritative source reports and the original v3.2 dependency link remain required.

## Exact-control path

With `signal_lag_days=1`, the diagnostic simulator must reproduce every yearly standard result from the authoritative v3.1.2/v3.2 reports within `1e-12`, including compounded return, cash return, action count and maximum drawdown.

Failure stops the diagnostic.

## One-day-delay path

The delayed path changes only information timing:

- original decision on execution day D uses features through D-1;
- delayed decision on execution day D uses features through D-2;
- fills, daily risk checks, scheduled cadence, natural drift, costs, cash yield and final liquidation remain unchanged.

The material gate uses conservative delayed excess over yielding cash: the lower compounded delayed excess from Binance and Coinbase. It must be strictly positive.

## Decision-interval contribution

A decision interval begins whenever target-changing turnover is nonzero. The new action's trading cost belongs to the new interval. The interval compounds strategy and cash returns until the next target-changing action. Final liquidation cost belongs to the final open interval.

Interval contribution is:

`interval strategy compounded return - interval cash compounded return`

Only positive interval contributions enter the concentration denominator. The diagnostic records the maximum positive interval share. The conservative gate uses the larger share across Binance and Coinbase and requires it to be no more than 20%.

This definition is stricter and more reproducible than inferring trades from asset names alone.

## Fail-closed rules

- no synthetic prices or missing days;
- no look-ahead;
- no threshold or model search;
- no result-dependent definition changes;
- exact-control mismatch is fatal;
- missing positive contributions fails the concentration gate;
- all outputs remain paper-only and cannot authorize trading.
