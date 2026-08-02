# v5.3 Untouched Nine-Month Replication Protocol

## Purpose

v5.3 performs one frozen evaluation of the top-ranked v5.2 mechanism on dates
that were not used to generate, deduplicate, screen, attack or rank any v5.2
candidate.

The candidate evaluation period is October 1, 2025 through June 30, 2026.
The v4.4 baseline has previously been measured on these dates, but candidate
activity and candidate returns have not.

This is paper-only research. No outcome authorizes live trading.

## Frozen primary mechanism

The primary mechanism is fixed exactly as ranked first by v5.2:

- Family: trend state.
- Source: cross-asset mean seven-day spot return.
- Transform: ten-day acceleration.
- Normalization: rolling 90-day percentile.
- Reference window: prior completed observations only; current value excluded.
- Event: upward crossing of percentile 0.30.
- Persistence: seven calendar observations.
- Exposure multiplier: 0.75.

The mechanism may only reduce targets already selected by frozen v4.4.

## Frozen corroboration mechanism

The v5.2 second-ranked breadth-reversal mechanism may be evaluated in the same
one-shot run for corroboration only:

- Source: positive breadth of 50-day moving-average distance.
- Transform: ten-day change.
- Normalization: rolling 20-day percentile using prior observations only.
- Event: downward crossing of percentile 0.30.
- Persistence: seven observations.
- Exposure multiplier: 0.75.

Its result cannot replace, rescue or alter the primary mechanism decision.

## Frozen model and execution

- Reuse the exact final v4.3 bundle trained through June 30, 2025.
- No retraining, recalibration, parameter search or candidate substitution.
- Reuse v4.4 cash yield and source availability rules.
- Completed daily candles only; fills remain at the next daily open.
- Standard one-way cost: 0.10%.
- Stress one-way cost: 0.20%.
- Universe, ranking, target size and three-day rebalance cadence remain frozen.
- Activity is computed continuously across the full nine-month interval so
  quarter and sealed-window boundaries cannot restart an event.

## Frozen reporting windows

Primary results are reported over:

1. Continuous interval: October 1, 2025 through June 30, 2026.
2. Calendar quarter 2025-Q4.
3. Calendar quarter 2026-Q1.
4. Calendar quarter 2026-Q2.
5. The five pre-existing sealed windows in `distributional_utility_v43.py`.

All windows use the same continuously generated activity map.

## Primary untouched-replication gates

The primary mechanism passes untouched replication only if every condition is
true:

- continuous standard excess return is positive;
- continuous stress excess return is positive;
- at least two of three calendar quarters have positive standard excess;
- at least three of five sealed windows have positive standard excess;
- no calendar-quarter standard excess is below -0.25%;
- no sealed-window standard excess is below -0.25%;
- at least three decisions are attenuated;
- maximum drawdown is not worse than baseline by more than 0.25 percentage
  points under either cost model;
- actions do not increase, assets are never added and targets never increase.

A predeclared one-day delayed version is diagnostic and must have continuous
standard excess of at least -0.10%. It cannot replace the frozen primary rule.

Passing these gates establishes an untouched mechanism replication. It does
not by itself establish the existing full profitability breakthrough.

## Profitability status

The unchanged v4.4 historical profitability gates are computed separately.
No gate threshold may be weakened. Independent-source replication and a
current-market paper smoke test remain required before deployment.

## Failure behavior

If the primary fails any untouched-replication gate, v5.3 records a clean
replication failure and retains v4.4 without modification. A passing secondary
mechanism cannot rescue a failed primary.

No candidate may be changed after any untouched result is calculated. Any new
hypothesis requires a new version and a newly designated future observation
period.

No live trading is authorized under any outcome.
