# v4.8 Dollar/Rates Exposure Attenuation Protocol

## Objective

Test whether the only promising v4.7 independent macro family can improve the verified v4.4 baseline when used as a gradual downside-only exposure control rather than an all-or-nothing cash gate.

v4.7 found that the `dollar_rates` family produced positive compounded validation excess with very small worst-fold damage, but its binary exit helped in too few folds. v4.8 is an exposed follow-up experiment and remains explicitly retrospective.

## Frozen baseline

The baseline remains v4.4:

- exact v4.3 learned bundle and hard regime routing;
- completed crypto candles and next-open fills;
- 3-day rebalance cadence;
- baseline 5% target per selected asset;
- standard one-way cost 10 bps;
- stress one-way cost 20 bps;
- prior-day-known DGS3MO yield on idle cash;
- paper-only, long-or-cash, no live execution.

## Independent macro input

Reuse the v4.7 fixed public FRED histories and strict prior-day availability rules.

Only the predeclared `dollar_rates` feature family is used:

- broad-dollar 5-, 20-, and 60-calendar-day changes from `DTWEXBGS`;
- 10-year Treasury yield level and 5-, 20-, and 60-calendar-day changes from `DGS10`.

The model receives no asset identity, crypto price, funding, basis, open interest, flow, utility, rank, or sealed-window result.

## Model

For every walk-forward fold, fit the same small regularized logistic classifier used in v4.7 on unique dates available through the fold training end. The date-level label is whether the average cross-asset forward 3-day return is positive.

No model state is shared across folds.

## Attenuation action

At a scheduled non-panic rebalance, when macro probability is below the calibrated threshold, multiply every baseline target weight by one fixed attenuation multiplier.

Predeclared active multipliers:

- `0.25`
- `0.50`
- `0.75`

For example, multiplier `0.50` changes a baseline 5% per-asset target to 2.5%. Above the threshold, the multiplier is 1.0.

The layer must never:

- add or substitute an asset;
- increase selected cardinality;
- increase any target above baseline;
- change the rebalance cadence;
- override panic-to-cash;
- use a negative or leveraged position.

The disabled candidate always uses multiplier 1.0 and no threshold, reproducing v4.4 exactly.

## Fold-level threshold calibration

Reuse the six v4.6 walk-forward folds.

For each active multiplier and fold:

1. independently train and calibrate the frozen v4.3 base bundle;
2. fit the dollar/rates classifier using dates through the fold training end;
3. evaluate each fixed probability threshold `0.35, 0.40, 0.45, 0.50, 0.55, 0.60, 0.65` only on the fold base-calibration quarter;
4. split that calibration quarter into its three calendar months;
5. choose the threshold by the following order:
   - highest minimum monthly excess versus baseline;
   - highest compounded monthly excess;
   - lower maximum drawdown;
   - lower turnover;
   - fewer actions;
   - fewer attenuated decisions;
   - lower threshold;
6. freeze the threshold before the following validation quarter.

The active threshold grid excludes the disabled state. The disabled candidate is evaluated separately.

## Multiplier selection

An active multiplier is eligible only if, across the six validation folds:

- compounded excess versus the corresponding v4.4 fold baselines is positive;
- at least four of six folds have strictly positive excess;
- no fold excess is below -0.25%;
- aggregate actions do not exceed baseline;
- aggregate turnover does not exceed baseline;
- no fold drawdown exceeds baseline by more than 0.25%;
- at least one validation decision is attenuated.

Eligible multipliers are ordered by:

1. minimum validation-fold excess;
2. positive-excess fold count;
3. compounded validation excess;
4. worst validation return;
5. lower maximum drawdown;
6. lower turnover;
7. fewer actions;
8. fewer attenuated decisions;
9. higher retained exposure multiplier.

The disabled v4.4 baseline wins whenever no active multiplier is eligible.

## Final retrospective evaluation

After multiplier selection is frozen:

1. use the final frozen v4.3 bundle through 2025-06-30;
2. fit the dollar/rates classifier only through 2025-06-30;
3. choose the final threshold only from the three monthly blocks between 2025-07-01 and 2025-09-30;
4. evaluate the same five exposed sealed windows from 2025-10-01 through 2026-06-30.

All original profitability, diversity, concentration, action-count, cost, and drawdown gates remain unchanged.

## Acceptance

v4.8 is a historical breakthrough only if every original historical gate passes. Independent-source replication and current-market smoke remain false until separately completed.

If no active attenuation multiplier survives the six-fold rules, v4.8 must select the disabled baseline and report the negative result without relaxing the protocol.
