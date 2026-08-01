# v4.7 Residual Momentum Breakout Implementation Contract

## Scope

Implement one transparent, paper-only cross-asset rule family over public daily spot data. The family is independent of v4.3 model predictions and reuses only verified v4.4 cash accounting, costs, reporting boundaries, and the frozen source inventory.

## Required invariants

- Signals use completed day-D data only.
- Fill occurs at day D+1 open.
- Daily asset return is D+1 open to D+2 open.
- Maximum one selected asset and 5% target exposure.
- Three-day rebalance cadence.
- Long-or-cash only.
- Prior-day-known Treasury rate only.
- The sealed windows never influence threshold selection.

## Required functions

Expose deterministic, directly testable functions for:

- residual feature construction;
- cross-sectional percentile ranking;
- market risk gate and observable regime;
- continuation and breakout qualification;
- date-level top-one selection;
- costed yield-bearing simulation;
- blocked-fold eligibility and selection;
- sealed-window aggregation and v4.4 daily-return correlation.

## Source and data checks

The campaign must validate:

- the frozen v4.3 report hash;
- the frozen v4.3 bundle and sealed evaluation;
- exact public-source inventory;
- exact v4.3 dataset metadata;
- complete common daily dates for all five assets;
- finite OHLCV inputs;
- sufficient 120-day indicator history; and
- cash history beginning before the first simulated date.

## Determinism

- Percentile ranks use stable sorting.
- Asset ties resolve alphabetically.
- Configuration ties follow the protocol’s conservative deterministic order.
- No random estimator is fitted in v4.7.
- The complete 324-configuration grid is recorded with compact diagnostics.

## Failure behavior

Fail closed on invalid source/baseline evidence, missing dates, non-finite inputs, unavailable prior cash rate, empty configuration grid, or inconsistent return alignment.

If no configuration meets fold eligibility, the campaign still produces diagnostic evidence using the best lexicographic configuration and marks it ineligible. It must not promote that configuration as a candidate.

## Safety assertions

The report must contain:

- `paper_only: true`
- `authorizes_trading: false`
- `authorizes_shadow_paper: true`
- `retrospective: true`
- `untouched_historical_dates: false`

The source must not contain exchange private keys, order placement, or live execution code.
