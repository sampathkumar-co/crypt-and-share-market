# v4.9 Dollar/Rates Probability-Shock Protocol

## Objective

Test whether the independent dollar/rates signal is useful as a detector of rapid deterioration rather than as an absolute low-state classifier.

v4.8 produced positive compounded validation excess for every attenuation multiplier, but activated in only three of six folds and improved only two. The activation dates were identical across multipliers. v4.9 therefore changes only the trigger definition and freezes the attenuation strength.

This is an exposed retrospective follow-up. It must not relax any v4.4 profitability gate, v4.8 walk-forward eligibility rule, cost assumption, or safety boundary.

## Frozen baseline

The baseline remains v4.4:

- exact v4.3 learned bundle and hard regime routing;
- completed crypto candles and next-open fills;
- 3-day rebalance cadence;
- baseline 5% target per selected asset;
- standard one-way cost 10 bps;
- stress one-way cost 20 bps;
- prior-day-known DGS3MO yield on idle cash;
- paper-only, long-or-cash, no execution integration.

## Independent macro model

Reuse the v4.7 fixed public FRED histories and strict prior-day availability rules.

Use exactly the v4.7 `dollar_rates` feature columns:

- broad-dollar 5-, 20-, and 60-calendar-day changes from `DTWEXBGS`;
- 10-year Treasury yield level and 5-, 20-, and 60-calendar-day changes from `DGS10`.

For each fold, fit the same small regularized logistic classifier on unique date-level samples through the fold training end. The label remains whether the average cross-asset forward 3-day return is positive.

The classifier receives no asset identity, crypto feature, funding, basis, flow, utility, rank, or sealed-window result.

## Probability-shock families

Let `p(D)` be the dollar/rates classifier probability available for crypto decision date `D`, computed only from macro observations known by `D - 1 day`.

Test exactly three predeclared downside shock families:

1. `drop_5`: `p(D - 5 calendar days) - p(D)`.
2. `drop_20`: `p(D - 20 calendar days) - p(D)`.
3. `drawdown_20`: maximum probability available from `D - 20` through `D - 1` minus `p(D)`.

When an exact lookback date is absent, use the newest earlier crypto date. A shock score is clipped at zero, so improving probability never triggers attenuation.

No family may use future probability, a same-date future macro observation, or a sealed result.

## Attenuation action

Freeze the active attenuation multiplier at `0.50`.

At a scheduled non-panic rebalance, if the selected shock score is at least the calibrated threshold, multiply every baseline target weight by `0.50`. Otherwise preserve the baseline target.

The layer must never:

- add or substitute an asset;
- increase selected cardinality;
- increase any target above baseline;
- change the rebalance cadence;
- override panic-to-cash;
- use a negative or leveraged position.

The disabled candidate uses no shock family, no threshold, and multiplier `1.0`, reproducing v4.4 exactly.

## Fold-level threshold calibration

Reuse the six v4.6 walk-forward folds.

For each shock family and fold:

1. independently train and calibrate the frozen v4.3 base bundle;
2. fit the dollar/rates classifier using dates through the fold training end;
3. compute only causal probability histories;
4. evaluate the fixed shock-threshold grid `0.025, 0.05, 0.075, 0.10, 0.15, 0.20` on the fold base-calibration quarter;
5. divide that quarter into its three calendar months;
6. choose the threshold by:
   - highest minimum monthly excess versus baseline;
   - highest compounded monthly excess;
   - lower maximum drawdown;
   - lower turnover;
   - fewer actions;
   - fewer attenuated decisions;
   - higher threshold;
7. freeze the family threshold before the following validation quarter.

The disabled candidate is evaluated separately and is not part of the active threshold grid.

## Family selection

An active shock family is eligible only if, across the six validation folds:

- compounded excess versus the corresponding v4.4 fold baselines is positive;
- at least four of six folds have strictly positive excess;
- no fold excess is below `-0.25%`;
- aggregate actions do not exceed baseline;
- aggregate turnover does not exceed baseline;
- no fold drawdown exceeds baseline by more than `0.25%`;
- at least one validation decision is attenuated.

Eligible families are ordered by:

1. minimum validation-fold excess;
2. positive-excess fold count;
3. compounded validation excess;
4. worst validation return;
5. lower maximum drawdown;
6. lower turnover;
7. fewer actions;
8. fewer attenuated decisions;
9. family name for deterministic tie-breaking.

The disabled v4.4 baseline wins whenever no active family is eligible.

## Final retrospective evaluation

After family selection is frozen:

1. use the final frozen v4.3 bundle through 2025-06-30;
2. fit the dollar/rates classifier only through 2025-06-30;
3. choose the final shock threshold only from the three monthly blocks between 2025-07-01 and 2025-09-30;
4. evaluate the same five exposed sealed windows from 2025-10-01 through 2026-06-30 once under standard and stress costs.

All original profitability, diversity, concentration, action-count, cost, and drawdown gates remain unchanged.

## Acceptance

v4.9 is a historical breakthrough only if every original historical gate passes. Independent-source replication and current-market smoke remain false until separately completed.

If no active probability-shock family survives the six-fold rules, v4.9 must select the disabled baseline and publish the null result without relaxing this protocol.

No live trading is authorized.