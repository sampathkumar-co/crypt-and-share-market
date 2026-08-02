# v5.4 July 2026 Forward Paper-Smoke Protocol

## Purpose

v5.4 freezes the one-decision-day delayed form of the v5.2 primary mechanism
and evaluates it once on July 2026 data that was outside every prior source
boundary.

The repository source boundary before this protocol was June 30, 2026. No July
2026 market archive has been used by v5.2 generation or v5.3 evaluation.

This is a current-market paper smoke, not a profitability backtest and not live
trading authorization.

## Frozen candidate

Start with the exact v5.2 primary mechanism:

- cross-asset mean seven-day spot return;
- ten-day acceleration;
- rolling 90-day percentile using prior observations only;
- upward crossing of percentile 0.30;
- seven-observation persistence;
- target multiplier 0.75.

Shift the complete activity array forward by exactly one daily decision date,
inserting an inactive value at the beginning. No other parameter may change.

## Forward source boundary

- Evaluation dates: July 1 through July 31, 2026.
- Download public Binance daily spot, perpetual, funding and open-interest
  metric archives only after this protocol is committed.
- The five-asset universe remains BTC, ETH, SOL, XRP and ADA.
- At least 29 dates must be complete for all five assets.
- If fewer than 29 common dates are complete, the result is
  `FORWARD_SMOKE_DATA_INCONCLUSIVE` and no performance decision is made.
- The final evaluated date and every source hash must be reported.

## Frozen execution

- Reuse the final v4.3 bundle trained through June 30, 2025.
- Completed daily candles only; fills remain at the next daily open.
- Reuse v4.4 portfolio ranking, targets and three-day cadence.
- Standard one-way cost: 0.10%.
- Stress one-way cost: 0.20%.
- Candidate activity may only reduce an existing target.
- Carry forward the last prior-known June 2026 cash rate through July; do not
  introduce a newly observed rate after examining market performance.

## Current-market smoke gates

The smoke passes only if every condition is true:

- at least 29 common complete market dates are available;
- at least one selected rebalance is attenuated;
- continuous standard excess over v4.4 is positive;
- continuous stress excess over v4.4 is positive;
- standard and stress maximum drawdown are not worse by more than 0.25
  percentage points;
- target-changing actions do not increase under either cost model;
- no asset is added and no target is increased.

A zero-intervention month is `FORWARD_SMOKE_NO_SIGNAL`, not a pass or failure.

## Interpretation

Passing establishes only a current Binance paper-smoke signal for the delayed
mechanism. It does not repair the v5.3 quarter-consistency failure, satisfy the
5% annualized historical gate, or provide independent-source replication.

The accepted strategy remains v4.4 under every v5.4 outcome. Any deployment
still requires independent-source replication and additional forward paper
observation.

No live trading is authorized.
