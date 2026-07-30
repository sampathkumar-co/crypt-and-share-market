# Crypto Funding Promotion Audit v1.6.1 — Frozen Protocol

## Status and statistical role

This protocol is frozen before any v1.6.1 audit return is calculated. It is paper-only, long-or-cash and unleveraged. It cannot authorise real-money trading.

v1.6.1 is a pre-return consistency correction to v1.6: the defensive profile's per-asset cap is 12.5%, so two positions cannot violate the frozen 75% cash reserve. No return was calculated under the inconsistent v1.6 profile.

The candidate under audit is the exact unchanged v1.4.2 funding-exhaustion signal and exit. It was selected after observing positive diagnostic results in the v1.4.2 and v1.5 discovery datasets. Therefore, the six known discovery periods are not an independent performance test. v1.6.1 uses them only for promotion robustness checks. The unchanged final 720-bar holdout is the sole independent performance test.

No signal threshold, funding calculation, entry rule, exit rule, cost assumption or data value may be changed in this experiment.

## Immutable data

Use only the existing v1.4.2 frozen artifact:

- Workflow run: `30516539776`
- Artifact ID: `8749247737`
- Artifact digest: `sha256:491a9bc1e9b3213dfd39a6de39ab36a7e9d91dc7f6381eea695d80935f4526b4`
- Price manifest SHA-256: `cd862f6ad739921663d97b5eb5424b17ce6af2d07abd40a0521ba47c2b9e27eb`
- External manifest SHA-256: `cae89da7cc39353a51eba72cd14f1c66eeff88d059c32283d4248694e3bfa5ae`
- Canonical reloaded four-hour CSV fingerprint: `c37b0ff611acff419f654889f7fb69b70771aa6d84cbb450fc0817d3f9b3ecfa`

Refetching, substituting or repairing data differently is forbidden.

Universe: APT, ARB, AVAX, DOT, FIL, NEAR, OP and SUI.

Each asset has exactly 4,434 completed four-hour candles from `2023-11-15T00:00:00` through `2025-11-22T20:00:00`.

## Frozen temporal split

- Warm-up: 600 bars.
- Known promotion-audit periods: six contiguous, non-overlapping periods of 480 bars each.
- Known audit interval: `2024-02-23T00:00:00` through `2025-06-16T20:00:00`.
- Embargo: 234 bars, `2025-06-17T00:00:00` through `2025-07-25T20:00:00`.
- Independent final holdout: 720 bars, `2025-07-26T00:00:00` through `2025-11-22T20:00:00`.

The holdout remains inaccessible unless every promotion-audit gate passes. The promotion-audit workflow must not invoke holdout mode.

## Frozen candidate

The candidate is exactly the existing v1.4.2 `funding_only` sleeve under the calendar-normalised settled-funding model:

- seven-calendar-day funding mean using exactly 42 four-hour buckets;
- exactly 720 prior seven-day means for the 120-day reference distribution;
- missing settlement buckets treated as zero settled funding cashflow;
- negative current funding at or below its prior tenth percentile;
- price drawdown of at least 15% from the preceding 120-bar completed-close high;
- recent low condition and 12-bar EMA recovery cross;
- unchanged funding recovery, 72-bar EMA, 2.25 ATR stop and 60-bar maximum-hold exits;
- completed-candle signals and next-bar-open fills.

No new candidate generation or parameter search is permitted.

## Fixed sizing profiles

Evaluate exactly three predeclared sizing profiles. They are robustness diagnostics, not a selection grid.

### Primary balanced profile

- Maximum positions: 2.
- Maximum asset weight: 25%.
- Minimum cash reserve: 50%.
- Portfolio volatility target: 25% annualised.
- Maximum permitted period drawdown: 8%.

### Original exposure diagnostic

- Maximum positions: 3.
- Maximum asset weight: 25%.
- Minimum cash reserve: 25%.
- Portfolio volatility target: 30% annualised.

### Defensive exposure diagnostic

- Maximum positions: 2.
- Maximum asset weight: 12.5%.
- Minimum cash reserve: 75%.
- Portfolio volatility target: 20% annualised.

Existing drawdown brakes, crypto fees, slippage and tax models remain unchanged in all profiles.

## Cost stresses

For every period and profile, record:

- normal after-cost return;
- standard stress: normal return minus 15 basis points per unit of turnover;
- double-cost stress: normal return minus 30 basis points per unit of turnover.

## Promotion robustness tests

The primary balanced profile is subjected to:

1. Six known contiguous audit periods.
2. Eight leave-one-asset-out reruns.
3. Six leave-one-period-out aggregate checks.
4. The original and defensive sizing diagnostics.
5. Standard and double-cost stresses.
6. Cash and equal-weight buy-and-hold comparisons.

Leave-one-period-out checks remove one complete period from the six-period primary summary and recompute the average from the other five; no simulation is rerun or altered.

## Promotion acceptance gates

The candidate is promoted to the one-shot holdout only if every condition passes:

- Exactly six known audit periods are present and all six are active.
- At least four of six periods are profitable.
- Primary average, median and compounded returns are positive after normal costs.
- First-half and second-half average returns are positive.
- Average standard-stressed and double-cost-stressed returns are positive.
- Primary average return exceeds cash and equal-weight buy-and-hold.
- Worst period drawdown is at most 8%.
- At least six distinct assets are selected.
- Maximum traded-notional concentration in one asset is at most 35%.
- All six leave-one-period-out average returns are positive.
- At least six of eight leave-one-asset-out reruns have positive average returns.
- Both original-exposure and defensive-exposure profiles have positive normal, standard-stressed and double-cost-stressed average returns.

Failure of any gate keeps the final holdout locked and is preserved as valid negative evidence.

## One-shot independent holdout

If and only if the promotion audit passes, run the unchanged candidate once on the unchanged 720-bar holdout using only the primary balanced sizing profile. No redesign, threshold change, alternate profile, retry or replacement candidate is allowed.

The holdout is accepted only if every condition passes:

- At least two of three holdout periods are active.
- At least two of three holdout periods are profitable.
- Average and compounded returns are positive after normal costs.
- Average standard-stressed and double-cost-stressed returns are positive.
- Average return exceeds cash and equal-weight buy-and-hold.
- Worst period drawdown is at most 8%.
- At least three distinct assets are selected.
- Maximum traded-notional concentration in one asset is at most 45%.

A successful holdout authorises only a later shadow-paper stage, never live trading or a profit claim.
