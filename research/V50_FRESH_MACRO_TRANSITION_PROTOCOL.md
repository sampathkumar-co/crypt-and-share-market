# v5.0 Fresh Macro-Transition Attenuation Protocol

## Objective

Test whether the independent dollar/rates probability is useful only near the beginning of a deterioration episode.

v4.8 showed positive compounded excess but repeatedly attenuated a harmful prolonged low-state episode. v4.9 confirmed the duration problem: the 20-day drawdown family improved three folds, but 12 repeated attenuations in WF-2 caused a loss beyond the frozen allowance; the safer 5-day shock was too sparse.

v5.0 therefore returns to the absolute dollar/rates state but permits attenuation only during a short, predeclared causal window after a downward threshold crossing.

This is an exposed retrospective experiment. It must not relax any profitability, consistency, cost, drawdown, or safety gate.

## Frozen baseline and model

Reuse exactly:

- the v4.4 crypto model, hard regime routing, 3-day rebalance cadence, 5% per-selected-asset targets, costs, cash yield, and sealed windows;
- the v4.7 `dollar_rates` feature family and regularized logistic classifier;
- prior-day-known FRED observations only;
- the six v4.6 walk-forward folds;
- paper-only, long-or-cash execution semantics.

No crypto signal, utility, ranking, risk, portfolio, or cost parameter may change.

## Downward crossing

Let `p(D)` be the causal dollar/rates classifier probability available for crypto date `D`.

For a fixed threshold `T`, a downward crossing occurs on date `D` when:

- `p(D) < T`; and
- the immediately preceding available crypto-date probability is `>= T`.

The first available probability does not count as a crossing when no prior date exists.

A low-state episode remains open while `p(D) < T`. It is rearmed only after probability recovers to `>= T`; a later downward crossing may start a new episode.

## Fresh-transition families

Test exactly three fixed causal windows:

1. `fresh_3d`: active from the crossing timestamp through less than 3 elapsed calendar days.
2. `fresh_7d`: active from the crossing timestamp through less than 7 elapsed calendar days.
3. `fresh_14d`: active from the crossing timestamp through less than 14 elapsed calendar days.

At scheduled non-panic rebalances inside the active window, multiply every baseline target by the fixed multiplier `0.50`. Outside the window, preserve the baseline target even if the macro probability remains below threshold.

The active window must not restart while the same low-state episode persists.

## Threshold calibration

For each transition family and walk-forward fold, evaluate exactly the fixed absolute-probability thresholds:

`0.35, 0.40, 0.45, 0.50, 0.55, 0.60, 0.65`.

Use only the fold base-calibration quarter, split into three calendar-month blocks. Select the threshold by:

1. highest minimum monthly excess versus baseline;
2. highest compounded monthly excess;
3. lower maximum drawdown;
4. lower turnover;
5. fewer actions;
6. fewer attenuated decisions;
7. higher probability threshold.

The selected threshold and family are frozen before each validation quarter.

## Family eligibility and selection

An active transition family is eligible only if, across all six validation folds:

- compounded excess is positive;
- at least four folds have strictly positive excess;
- no fold excess is below `-0.25%`;
- aggregate actions do not exceed baseline;
- aggregate turnover does not exceed baseline;
- no fold drawdown exceeds baseline by more than `0.25%`;
- at least one decision is attenuated.

Eligible families are ordered by:

1. minimum fold excess;
2. positive-fold count;
3. compounded excess;
4. worst validation return;
5. lower maximum drawdown;
6. lower turnover;
7. fewer actions;
8. fewer attenuated decisions;
9. shorter transition window;
10. family name for deterministic tie-breaking.

The disabled v4.4 baseline is fallback only when no active family qualifies.

## Safety invariants

The transition layer must never:

- add or substitute an asset;
- increase selected cardinality;
- increase any target above the corresponding v4.4 target;
- override panic-to-cash;
- alter the rebalance cadence;
- create short or leveraged exposure;
- use future probabilities or same-day future macro data.

## Final retrospective evaluation

After family selection:

1. use the final frozen v4.3 bundle through 2025-06-30;
2. fit the macro classifier only through 2025-06-30;
3. calibrate the final threshold only on July–September 2025 monthly blocks;
4. evaluate the same five exposed sealed windows once under standard and stress costs.

If no active transition family survives the six-fold rules, select disabled and reproduce v4.4 exactly.

No live trading is authorized.