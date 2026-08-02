# v5.4.2 Forward Tail Integrity Correction Protocol

## Purpose

v5.4.1 loaded 31 complete July source dates, but the generic training-dataset builder reserved eight future days for labels. Its reported smoke therefore contained only 23 decision dates, ending on 2026-07-23.

This protocol preserves that result as a valid partial smoke and replaces only the forward inference boundary. It does not change the candidate, portfolio rules, costs, universe, model bundle, or July observations.

## Frozen candidate

- Family: trend-state exposure attenuation.
- Source: cross-asset mean seven-day spot return.
- Transform: acceleration with lag 10.
- Rolling history: 90 completed observations.
- Event: upward crossing of rolling percentile 0.30.
- Persistence: seven decision days.
- Activity delay: one decision day.
- Active exposure multiplier: 0.75.
- Long-or-cash and paper-only.
## Frozen inference semantics

For a decision dated D, features may use data dated no later than D. The portfolio fills at the next daily open D+1 and the one-day return is open(D+2) / open(D+1) - 1.

The corrected evaluation period is 2026-07-01 through 2026-07-30 inclusive. The July 30 decision therefore uses the real July 31 open as entry and the real August 1 open as exit. No August feature, funding, open-interest, flow, close, high, or low value may enter a July feature row.

The tail builder must create inference rows without requiring three-day or seven-day future labels. Non-inference target arrays may contain explicit neutral sentinels because they are not consumed by prediction, decision generation, or simulation.

## Source boundary

- Preserve the exact through-June frozen source bundle.
- Preserve the exact v5.4.1 July source inventory and observations.
- Download only five Binance spot daily kline archives for 2026-08-01, one per asset.
- Use only each archive's August 1 open to complete July 30 return1.
- Record every URL and SHA-256 digest.
- Missing or malformed August 1 data makes the run data-inconclusive.
## Integrity requirements

Before evaluation, compare every date-asset row shared with the frozen v4.2 builder. Feature vectors, one-day returns, feature names, dates, and asset ordering must match exactly; any mismatch invalidates the run.

The corrected dataset must contain exactly five rows per decision date and exactly 30 decision dates from July 1 through July 30. July 31 is source context only and must not become a decision row.

The frozen v4.3 bundle is not retrained. Candidate activity is not recalibrated. The v4.4 cash-yield policy and standard/stress costs remain unchanged.

## Pass gates

A pass requires all of the following:

- 30 genuine July decision dates and complete August 1 exit opens.
- Exact overlap with the generic historical dataset.
- At least one attenuated selected rebalance.
- Positive standard and stress excess return versus v4.4.
- Standard and stress drawdown no more than 25 basis points worse.
- Target-changing actions do not increase.
- No asset is added and no target exceeds the baseline.

Possible statuses are `FORWARD_TAIL_DATA_INCONCLUSIVE`, `FORWARD_TAIL_NO_SIGNAL`, `FORWARD_TAIL_PASSED`, and `FORWARD_TAIL_FAILED`.
