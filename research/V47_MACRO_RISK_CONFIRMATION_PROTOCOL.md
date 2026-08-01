# v4.7 Macro-Risk Confirmation Protocol

## Research question

Can independently sourced macro-risk information improve the frozen v4.4 crypto strategy without fitting another threshold around the same Binance-derived features?

## Control

The control is the exact reproduced v4.4 yield-bearing-cash strategy:

- frozen v4.3 learned decisions
- completed daily crypto observations
- next-bar-open fills
- three-day rebalance cadence
- long-or-cash only
- 5% target value per selected asset
- standard one-way cost 0.10%
- stress one-way cost 0.20%
- prior-day-known DGS3MO yield on idle cash

## Independent data

v4.7 adds three FRED daily series:

- `VIXCLS`: CBOE Volatility Index
- `DTWEXBGS`: Nominal Broad U.S. Dollar Index
- `DFII10`: 10-Year Inflation-Indexed Treasury Constant Maturity Yield

The fixed source interval is 2020-01-01 through 2026-06-30. Raw payloads, hashes, observation counts and first/last dates must be recorded.

## Publication-lag rule

For crypto decision date `D`, each macro component may use only an observation dated on or before `D-1`.

Same-day macro observations are forbidden. A component older than seven calendar days fails closed.

## Signal

For each decision date, construct a macro-stress score from three trailing-percentile components:

1. current VIX level percentile over its trailing 252 valid observations
2. 60-observation broad-dollar return percentile over its trailing 252 valid transformed observations
3. 20-observation real-yield change percentile over its trailing 252 valid transformed observations

Each component is bounded to `[0, 1]`. The macro-stress score is their arithmetic mean.

Low stress is supportive; high stress is defensive.

## Allowed action

The macro signal may change target size only at the existing three-day rebalance point:

- supportive state: multiply the frozen 5% per-asset target by a bounded supportive multiplier
- neutral state: retain the 5% target
- defensive state: multiply the target by a bounded defensive multiplier

It may not select a new asset, change asset ranking, short, borrow, act off cadence or exceed 15% aggregate target exposure.

## Pre-registered grid

- supportive threshold: `0.30` or `0.40`
- defensive threshold: `0.60` or `0.70`
- supportive multiplier: `1.00`, `1.25` or `1.50`
- defensive multiplier: `0.00`, `0.50` or `0.75`
- exact disabled baseline: supportive and defensive multipliers both `1.00`

## Selection

Selection uses the six v4.6 walk-forward folds. Each fold independently refits and calibrates its v4.3 base bundle before the following validation quarter.

An active macro configuration is eligible only when:

- at least four of six standard-cost folds have positive excess versus their v4.4 control
- compounded standard excess is positive
- compounded stress excess is positive
- worst standard fold excess is no worse than -0.30%
- no fold drawdown exceeds its control by more than 0.50%
- maximum target exposure is at most 15%

If no active configuration is eligible, select the disabled v4.4 baseline.

Among eligible active configurations, prioritize:

1. worst standard fold excess
2. positive standard fold count
3. compounded stress excess
4. compounded standard excess
5. lower maximum drawdown
6. lower turnover and lower intervention complexity

## Sealed evaluation

After selection is frozen, evaluate the existing five exposed sealed windows exactly once and report the unchanged profitability gates.

## Safety status

This protocol is retrospective, paper-only and does not authorize live trading. Historical sealed dates are already exposed and must remain labeled `untouched_historical_dates=false`.
