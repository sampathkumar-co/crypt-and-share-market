# v6.0.2 Champion Statistical Gates Protocol

Status: frozen after the material gates passed and before statistical outcomes are calculated.

## Objective

Determine whether the unchanged v3.1.2/v3.2 champion's excess over yielding cash remains statistically credible after serial dependence and the project's large hypothesis search history are considered.

This stage cannot change the strategy or material gates and cannot authorize trading.

## Aligned daily relative-excess series

For each frozen source, reproduce the lag-one standard-cost path and build the exact daily relative return versus yielding cash:

`relative_day = (1 + strategy_day_return) / (1 + cash_day_return) - 1`

Final liquidation cost is represented as an additional relative-return observation against zero cash return. The five annual verification windows are concatenated in chronological order. The Binance and Coinbase arrays must align exactly in length.

The conservative series uses the lower relative return from Binance and Coinbase at every aligned observation. This is a deliberately pessimistic synthetic lower-bound path and is not presented as an executable exchange path.

## Moving-block bootstrap

Use a deterministic circular moving-block bootstrap with:

- block lengths: 20, 60 and 120 observations;
- 10,000 resamples per block length;
- seed derived from the frozen v3.1.2, v3.2, v6.0.1.1 and this protocol's SHA-256 values;
- compounded relative return as the statistic;
- 2.5th percentile as the 95% lower bound.

The bootstrap gate passes only when the lower bound is strictly positive for all three block lengths.

## Deflated Sharpe floor test

Compute annualized Sharpe from the conservative daily relative-return series using 365 periods per year, along with sample skewness and excess kurtosis.

Use the Bailey/Lopez de Prado expected-maximum-Sharpe approximation and the same finite-sample non-normality correction already implemented in the v6 Foundry migration.

The project has at least 100,000 attempted configurations because v5.2 alone generated that many hypotheses. Therefore:

- calculate a DSR probability with `number_of_trials = 100000` and `sharpe_trial_std = 1.0`;
- require probability >= 0.95;
- treat this as a floor test, not the final complete-registry result.

If the champion fails with only this lower-bound trial count, the DSR gate is definitively failed. If it passes, the complete append-only trial registry is still required before promotion.

## PBO boundary

CSCV Probability of Backtest Overfitting remains pending until all frozen tournament arms have aligned daily return series under common accounting. It may not be guessed from yearly aggregates.

## Outcome

- `STATISTICAL_GATES_FAILED`: block bootstrap or the 100,000-trial DSR floor fails.
- `PBO_AND_COMPLETE_REGISTRY_PENDING`: bootstrap and DSR floor pass, but complete registry/PBO are absent.
- `HISTORICAL_BREAKTHROUGH_CANDIDATE`: reserved for a later report in which every material and statistical gate passes.

No outcome here changes v3.3 or authorizes live/continuous paper trading.
