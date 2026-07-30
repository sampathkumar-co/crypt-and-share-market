# Crypto Funding-State Alpha v1.5 — Frozen Protocol

## Status

This protocol is frozen before any v1.5 strategy return is calculated. It is paper-only, long-or-cash, unleveraged research. It cannot authorise real-money trading.

v1.5 is a distinct follow-up to the rejected v1.4.2 multi-regime experiment. v1.4.2 showed that its range sleeve was consistently harmful while its diagnostic funding-only sleeve was positive. v1.5 therefore tests one narrowly defined funding-state family rather than retuning or combining the rejected sleeves.

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
- Discovery: six contiguous, non-overlapping periods of 480 bars each.
- Discovery interval: `2024-02-23T00:00:00` through `2025-06-16T20:00:00`.
- Embargo: 234 bars, `2025-06-17T00:00:00` through `2025-07-25T20:00:00`.
- Final holdout: 720 bars, `2025-07-26T00:00:00` through `2025-11-22T20:00:00`.

The holdout remains inaccessible unless every discovery gate passes. Discovery workflows must not invoke holdout mode.

## Execution contract

- Signals use completed candles and completed four-hour funding buckets only.
- Orders fill at the next bar open.
- Existing crypto fees, slippage and tax models are unchanged.
- Long-or-cash only.
- No leverage or shorting.
- Maximum two simultaneous positions.
- Maximum 25% portfolio weight per asset.
- Minimum 50% cash reserve.
- Portfolio volatility target: 25% annualised.
- Existing drawdown brakes remain active.
- Extra cost stress: 15 basis points per unit of turnover.

## Funding-state construction

For each asset and completed four-hour timestamp:

1. Fill absent funding-settlement buckets with zero cashflow, never with future values.
2. Compute the mean settled funding over exactly 42 four-hour buckets, representing seven calendar days.
3. Compare the current seven-day mean with exactly 720 prior seven-day means, representing 120 calendar days.
4. Record the prior-history percentile rank, fifth percentile, fifteenth percentile and median.
5. Require the full 120-day prior history before a state is usable.

## Primary signal: cross-sectional funding exhaustion with price recovery

An asset is eligible only when all conditions hold:

- Current seven-day funding mean is negative.
- Its prior-history percentile rank is at or below 15%.
- It ranks among the three most depressed funding states in the current eight-asset universe.
- Price is at least 12% below the highest completed close of the preceding 120 bars.
- ATR over 20 bars is positive.
- Price recovery confirmation has at least two of these three conditions:
  - completed close is above its 12-bar EMA;
  - completed close is above the preceding bar high;
  - completed three-bar return is positive.

Eligible assets are ranked by a fixed score combining funding-percentile depth, cross-sectional funding rank, drawdown magnitude and recovery strength. The top two are deployable.

## Frozen diagnostic variants

No parameter search is permitted. Evaluate exactly these variants:

1. `primary_consensus`: all primary conditions.
2. `without_cross_sectional_rank`: removes only the current-universe bottom-three requirement.
3. `without_price_recovery`: removes only the two-of-three price recovery requirement.
4. `deep_extreme`: requires a prior-history percentile rank at or below 5%, a 15% drawdown and price recovery; it does not require the cross-sectional bottom-three rule.
5. `legacy_funding_only`: the unchanged v1.4.2 funding-only rule under the same calendar-normalised funding model and v1.5 portfolio limits.
6. `cash`.
7. `equal_weight_buy_hold`.

## Exit rules

Exit a funding-state position at the next bar open when any condition was true on the preceding completed bar:

- Current seven-day funding mean reaches or exceeds its 120-day rolling median.
- Funding percentile rank rises above 50%.
- Close reaches or exceeds the 72-bar EMA.
- Close falls more than 2.25 entry ATR below average entry price.
- Maximum holding time reaches 60 bars.

## Discovery acceptance gates

The primary is accepted only if every condition passes:

- Exactly six discovery periods are present.
- At least five periods are active.
- At least four periods are profitable.
- Average, median and compounded returns are positive after normal costs.
- Average return remains positive under the extra-cost stress.
- First-half and second-half average returns are positive.
- Average return exceeds cash, equal-weight buy-and-hold and `legacy_funding_only`.
- Primary beats `legacy_funding_only` in at least four of six periods.
- Worst period drawdown is at most 10%.
- At least five distinct assets are selected.
- Maximum traded-notional concentration in one asset is at most 35%.
- At least two of the three structural diagnostics (`without_cross_sectional_rank`, `without_price_recovery`, `deep_extreme`) have positive average returns.
- At least six of eight leave-one-asset-out primary reruns have positive average returns.

Failure of any gate keeps the final holdout locked and is preserved as valid negative evidence.

## Holdout rule and acceptance gates

If and only if discovery passes, run the primary once on the unchanged 720-bar holdout. No redesign, threshold change, retry or alternate variant is allowed after discovery acceptance.

The one-shot holdout is accepted only if every condition passes:

- At least two of three holdout periods are active.
- At least two of three holdout periods are profitable.
- Average and compounded returns are positive after normal costs.
- Average return remains positive under the extra-cost stress.
- Average return exceeds both equal-weight buy-and-hold and `legacy_funding_only`.
- Worst period drawdown is at most 10%.
- At least three distinct assets are selected.
- Maximum traded-notional concentration in one asset is at most 45%.

A successful holdout still authorises only a later shadow-paper stage, never real-money trading.
