# v2.5 High-Conviction Forward Alpha Protocol

## Status and objective

This protocol is frozen before v2.5 implementation and before any v2.5 return,
P&L, drawdown, benchmark result, confidence interval or holdout outcome is
calculated. The objective is to test three distinct event-driven long-or-cash
families using only future append-only v2.0 snapshots.

v2.5 is isolated from the frozen v2.3/v2.4 campaign. It may read the same
canonical v2.0 public market-state observations, but it must not modify v2.3
source, protocol, decisions, manifests, readiness, evaluator, gates or holdout.

## Immutable safety boundary

- Paper research only; `authorizes_trading=false`.
- Long-or-cash only; no shorts, leverage, derivatives orders or borrowing.
- No wallet, exchange credential, API secret or live-order code.
- Maximum two selected assets.
- Maximum 15% weight per asset and 30% total intended exposure.
- At least 70% intended cash at every decision.
- Completed hourly snapshots only; decision at hour `t`, earliest fill at `t+1`.
- Missing, stale, malformed or discontinuous evidence fails closed to cash.

## Input and warm-up

- Universe: BTC, ETH, SOL, AVAX, LINK and DOGE.
- Inputs: canonical earliest-valid v2.0 snapshots on `forward-data/v2`.
- Exactly 169 contiguous hourly snapshots are required before candidate logic.
- Feature percentiles, betas and normalization statistics use only observations
  ending at or before the completed decision hour.
- A v2.5 decision references every canonical snapshot identifier and record hash
  used by the decision.

## Family 1: residual momentum with microstructure confirmation

For each non-BTC asset, estimate its hourly beta to BTC from the prior 168
completed one-hour returns. Residual momentum is asset return minus beta times
BTC return.

A candidate requires all of:

- positive raw six-hour and 24-hour spot returns;
- six-hour residual return at least 0.8%;
- 24-hour residual return at least 1.5%;
- both residual returns at or above their trailing 80th percentiles;
- spot taker imbalance at least 0.12;
- spot-book imbalance at least 0.04;
- spot flow exceeds perpetual flow by at least 0.08;
- absolute basis no greater than 20 bps and funding no greater than 0.010%;
- six-hour open-interest growth between -3% and +8%;
- global risk controls permit positive exposure.

## Family 2: funding/basis state transition

This family trades the transition out of a derivatives-led stress state rather
than the static extreme itself. A candidate requires all of:

- prior six-hour funding at or below its trailing 10th percentile, or prior
  six-hour basis at or below its trailing 10th percentile and below zero;
- current basis improves by at least 3 bps from the prior hour;
- current funding is no worse than the prior hour and is at most 0.005%;
- six-hour open interest contracts by at least 3%;
- latest one-hour spot return is positive;
- spot taker imbalance at least 0.10;
- spot-book imbalance at least 0.03;
- spot flow exceeds perpetual flow by at least 0.05;
- current basis is no greater than +10 bps;
- global macro control is not blocked.

## Family 3: sweep-and-replenishment continuation

The v2.0 hourly snapshots cannot observe individual millisecond sweeps. The
frozen forward proxy therefore requires a completed prior-hour liquidity shock
followed by current-hour book replenishment and persistent spot leadership.

A candidate requires all of:

- prior-hour spot spread at or above its trailing 80th percentile;
- prior-hour total top-ten spot-book notional at or below its trailing 20th
  percentile;
- current total spot-book notional improves at least 25% from the prior hour;
- current spread contracts at least 20% and is no greater than 15 bps;
- current spot taker imbalance at least 0.15;
- current spot-book imbalance at least 0.05;
- spot flow exceeds perpetual flow by at least 0.10;
- positive one-hour and three-hour spot returns, with three-hour return no
  greater than 5%;
- absolute basis no greater than 20 bps and funding no greater than 0.010%;
- six-hour open-interest growth no greater than 8%.

## Scoring, selection and cost hurdle

Each family score is a deterministic sum of capped normalized condition
strengths. Within each asset only the highest-scoring family is retained.
Candidates are then sorted by descending score, asset and family.

A candidate must also clear a frozen edge-to-cost hurdle: its normalized event
amplitude must be at least three times the standard 20 bps round-trip cost.
The precise amplitude is residual six-hour return for family 1, the absolute
prior dislocation plus current basis improvement for family 2, and the smaller
of three-hour return and spread contraction for family 3.

Select at most two assets. A second candidate is rejected when its trailing
seven-day hourly spot-return correlation with the first exceeds 0.80.
Each selected candidate receives 15% intended weight, subject to the inherited
global exposure cap. Any unused allocation remains cash.

## Cooldown and event uniqueness

The decision report includes a deterministic event key from asset, family and
trigger-state hashes. Evaluation treats repeated same-family signals for one
asset within four hours as one event: only the earliest decision may open a
cohort. This cooldown is accounting logic, not mutable signal tuning.

## Frozen evaluation design

Evaluation is separately implemented only after its protocol and fingerprints
are frozen. It must require 1,448 contiguous eligible decision hours:

- decisions 1-1,104: discovery;
- decisions 1,105-1,112: purge/embargo;
- decisions 1,113-1,448: untouched 336-hour holdout.

Primary holding horizon is four hours; two and eight hours are sensitivities.
Standard friction is 20 bps round trip and stress friction is 40 bps.
Before the complete requirement exists, only readiness, coverage and activity
may be emitted; no performance field may be calculated or persisted.

## Mandatory discovery gates

All conditions must pass after standard costs unless stated otherwise:

- positive compounded return and positive stress-cost return;
- positive return in both chronological discovery halves;
- non-negative stress return in both halves;
- at least 100 active event decisions on at least 24 UTC days;
- all three families active and at least four assets selected;
- positive 95% lower confidence bound for mean daily return using the frozen
  three-day circular moving-block bootstrap, 20,000 resamples, seed 2505;
- maximum drawdown no greater than 10%;
- beat cash, 30%-exposure BTC and 30%-exposure equal-weight universe;
- at least four of six leave-one-asset-out runs positive;
- at least two of three leave-one-family-out runs positive;
- no asset contributes more than 45% and no family more than 55% of positive P&L;
- primary four-hour horizon positive and at least one sensitivity positive;
- each family-specific ablation must show positive incremental contribution:
  family 1 without residualization, family 2 without state transition, and
  family 3 without replenishment confirmation may not outperform the primary.

Discovery rejection is terminal and must not read holdout decision, snapshot or
outcome bytes.

## Holdout gate

The one-shot holdout opens only after every discovery gate passes. It requires
at least 24 active events on seven UTC days, positive standard-cost return,
non-negative stress return, improvement over 30%-exposure equal weight, maximum
drawdown no greater than 10%, and no single day contributing more than 50% of
positive P&L.

Passing sets only `eligible_for_shadow_paper_review=true`. It does not authorize
shadow positions, continuous paper positions or real-money trading.

## Implementation sequence

1. Commit this protocol alone.
2. Implement decision-only v2.5 source and synthetic tests.
3. Add PR-only verification and an hourly append-only decision workflow.
4. Freeze and implement readiness-only validation before any outcome attachment.
5. Accumulate future evidence; never backfill missing hours.
6. Implement discovery and one-shot holdout only when their evidence boundaries
   are unlocked.
