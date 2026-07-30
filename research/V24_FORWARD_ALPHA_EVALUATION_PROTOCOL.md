# v2.4 Future-Only Forward Alpha Evaluation Protocol

## Objective

Evaluate the frozen v2.3 decision-only alpha candidates on genuinely future,
append-only observations without reusing retired historical OHLCV windows.

This protocol is committed before evaluator implementation and before any v2.3
return, P&L, drawdown, benchmark result or confidence interval is calculated.

## Immutable dependencies

- v2.3 source SHA-256:
  `1ad7cd99af39ae201a046737835b13f91216a724f4b5ade5292e86ed64bd04ba`
- v2.3 protocol SHA-256:
  `31b8fc6af9b3efe265b926849017737edc77fb2599ac328c1f219e4177841253`
- Universe: BTC, ETH, SOL, AVAX, LINK and DOGE.
- Candidate families and thresholds remain exactly those frozen in v2.3.
- Inputs are only canonical v2.0 snapshots and v2.3 decisions preserved on
  `forward-data/v2`.
- Every output remains paper-only with `authorizes_trading=false` and
  `authorizes_shadow_paper=false`.

## Activation and frozen timeline

- Activation is the first canonical v2.3 decision with exactly 169 contiguous
  input snapshots and an available next-hour v2.0 spot-mid snapshot.
- The activation decision/report/file hashes and next-hour snapshot hashes are
  permanently locked before any outcome calculation.
- Exactly 1,440 contiguous hourly decision intervals form the campaign.
- Decisions 1–1,104 are discovery.
- Decisions 1,105–1,440 are the untouched 336-hour promotion holdout.
- The primary four-hour exit needs snapshots through decision hour `t+5`.
- Sensitivity exits need snapshots through `t+3` and `t+9`.
- The complete campaign therefore requires all decision hours plus every
  next-hour entry and every exit snapshot through the final `t+9`.
- Missing required hours make the block incomplete; cash output from a missing
  decision is not fabricated.

Before all 1,440 decision hours and required snapshots exist, only readiness,
coverage and activity diagnostics may be emitted. No performance field may be
calculated, printed or persisted.

## Non-levered overlapping-cohort accounting

The v2.3 protocol permits overlapping hourly observations. To keep the paper
portfolio long-or-cash and non-levered, each decision opens a staggered cohort:

- For holding horizon `H`, every target weight is divided by `H` at entry.
- Primary horizon: `H=4`; sensitivities: `H=2` and `H=8`.
- With continuous maximum 40% decisions, at most `H` cohorts overlap and the
  intended aggregate target exposure remains no greater than 40%.
- At least 60% intended cash remains; buys are additionally capped by available
  cash, so actual leverage is impossible.
- Entry uses the earliest valid spot mid in hour `t+1`.
- A cohort quantity is fixed at entry and exits completely at hour `t+1+H`.
- A missing asset/family in an ablation leaves its cohort allocation in cash.
- Existing cohorts are never resized because of a later decision.
- Each asset/family cohort is tracked independently for contribution analysis.

Standard execution friction is 10 basis points at entry plus 10 basis points at
exit. Stress friction is 20 basis points each way, preserving the frozen 20/40
basis-point round-trip assumptions.

## Portfolio return and benchmarks

- The paper wallet starts at INR 100,000 equivalent.
- Open cohorts are marked each hour using the canonical v2.0 spot mid.
- Portfolio return compounds through the shared cash wallet and overlapping
  cohorts; it is not a product of independently reused decision observations.
- Drawdown uses the hourly marked-equity curve.
- All remaining cohorts are liquidated at the frozen final mark using the same
  exit friction.
- The v2.3 gate is evaluated after execution friction and before income-tax/TDS
  overlays because v2.3 froze only the 20/40 basis-point cost model.
- A separate tax sensitivity may be reported but cannot determine acceptance.

Benchmarks use the same INR 100,000 wallet and 40% maximum exposure:

- Cash: 100% cash.
- Equal-weight universe: 40% split equally across six assets, 60% cash.
- BTC: 40% BTC, 60% cash.
- Selected-assets passive: 40% split equally across assets selected at least
  once in discovery, 60% cash.
- Passive benchmarks enter at the discovery first fill, exit at the discovery
  final mark, and pay the same standard or stress round-trip friction.

## Daily aggregation and uncertainty

- Hourly marked equity is sampled at the final available mark of each UTC day.
- Daily returns are computed from consecutive UTC day-end equity values.
- The discovery confidence interval uses deterministic UTC day-block bootstrap:
  20,000 resamples, sample size equal to the observed discovery-day count and
  frozen random seed `2304`.
- The reported 95% interval is the 2.5th and 97.5th percentiles of mean daily
  return resamples.
- The lower bound must be strictly positive for discovery acceptance.
- A day with no active cohort remains a valid zero-return day.

Contribution limits use realized positive cohort P&L after execution friction:

- no asset may contribute more than 45% of positive discovery P&L;
- no family may contribute more than 55%;
- if total positive contribution is zero, both concentration gates fail.
- Holdout day concentration uses realized positive daily portfolio P&L; no
  single UTC day may contribute more than 50%.

## Frozen discovery gate

All v2.3 conditions remain mandatory:

- at least 100 active decisions on at least 24 UTC days;
- at least four selected assets and all three active families;
- positive standard- and stress-cost compounded returns;
- positive standard-cost return in both chronological halves;
- non-negative stress-cost return in both halves;
- positive 95% bootstrap lower bound for mean daily return;
- maximum drawdown no greater than 10%;
- beats cash, 40%-exposure equal-weight universe, selected-assets passive and
  40%-exposure BTC under standard costs;
- beats equal-weight universe under stress costs;
- at least four of six leave-one-asset-out runs positive under standard costs;
- at least two of three leave-one-family-out runs positive;
- asset/family positive-P&L concentration limits pass;
- primary four-hour horizon is positive and at least one of two/eight hours is
  positive.

Discovery rejection is terminal and must not read, hash, parse or summarize
holdout decision or snapshot bytes.

## Frozen holdout gate

Only after discovery passes may the following 336 decision hours be opened once.
The holdout requires:

- at least 24 active decisions on at least seven UTC days;
- positive standard-cost compounded return;
- non-negative stress-cost compounded return;
- standard-cost return above the 40%-exposure equal-weight universe;
- maximum drawdown no greater than 10%;
- no single UTC day contributes more than 50% of positive P&L.

A terminal discovery or holdout result is written once with its report hash,
configuration, implementation fingerprints and exact input-file inventory.
Later runs verify and replay that terminal result without reopening scored files.

Passing sets only `eligible_for_shadow_paper_review=true`. It never authorizes
shadow paper, continuous paper positions or real-money trading.

## Implementation sequence

1. Commit this protocol before evaluator implementation.
2. Implement readiness-only status and exact activation locking.
3. Verify no performance fields exist before the full 1,440-hour campaign.
4. Implement discovery accounting without any holdout byte access.
5. Implement one-shot holdout and immutable terminal-result persistence.
6. Run only against `forward-data/v2`; never backfill historical outcomes.
