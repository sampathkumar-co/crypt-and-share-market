# v5.1 Consensus Transition-Threshold Protocol

## Objective

Test whether v5.0 failed because its final-quarter threshold recalibration was unstable rather than because the `fresh_14d` mechanism lacked walk-forward validity.

v5.0 selected `fresh_14d` through the frozen six-fold rules with four positive folds, no negative fold, and positive compounded validation excess. Its six independently calibrated thresholds were:

`0.60, 0.55, 0.65, 0.65, 0.60, 0.50`.

The final July–September 2025 quarter then selected `0.50`, the lowest threshold observed among the folds, and the sealed result underperformed v4.4.

v5.1 does not choose a different family, multiplier, feature set, or portfolio rule. It changes only how the final threshold is frozen.

This is an exposed retrospective follow-up. The consensus procedure must be fixed before another sealed evaluation, and the resulting threshold may not be altered afterward.

## Frozen inherited components

Reuse exactly:

- v4.4 crypto signals, bundle, hard regime routing, costs, 3-day cadence, targets, cash yield, universe, and acceptance gates;
- v4.7 dollar/rates features, model, labels, and prior-day-known FRED availability;
- v5.0 causal downward crossing and episode-rearm semantics;
- transition family `fresh_14d` only;
- active multiplier `0.50`;
- the six v4.6 walk-forward folds;
- paper-only, long-or-cash operation.

No other candidate family, threshold grid, multiplier, asset rule, or signal modification is allowed.

## Required v5.0 reproduction

Before deriving a consensus threshold, v5.1 must independently reproduce the six `fresh_14d` fold-calibration thresholds using the v5.0 protocol.

The reproduced threshold list must be preserved in chronological fold order. The v5.0 family must still satisfy its original eligibility rules when evaluated with its independently calibrated per-fold thresholds.

If that reproduction or eligibility check fails, v5.1 must stop and publish a failed-reproduction result without evaluating sealed data.

## Consensus procedure

Derive one final threshold using only the six reproduced fold-calibration thresholds. These calibration periods all end no later than 2025-06-30.

Procedure:

1. sort the six thresholds in ascending order;
2. take the ordinary statistical median;
3. if the median lies between two values on the frozen v5.0 threshold grid, round upward to the next grid value;
4. freeze that threshold globally.

The upward rule is predeclared because attenuation is a downside intervention: a higher state threshold requires a stronger recovery probability before a downward crossing can trigger and avoids expanding intervention frequency through a lower off-grid midpoint.

No validation return, July–September 2025 result, sealed-window result, or current-market outcome may enter this calculation.

## Consensus walk-forward audit

After the global threshold is frozen, evaluate `fresh_14d` with that same threshold on all six validation folds.

This audit is not allowed to select or modify the threshold. It must report the same safety and economic metrics as v5.0.

The consensus rule is eligible for final evaluation only if its six validation folds satisfy the original v5.0 eligibility rules:

- positive compounded excess;
- at least four strictly positive folds;
- no fold excess below `-0.25%`;
- no aggregate action or turnover increase;
- no drawdown allowance violation;
- at least one attenuated decision;
- no asset addition, target increase, short, or leverage.

If the fixed consensus rule fails, use the disabled v4.4 baseline and do not evaluate an active rule on sealed data.

## Final pre-sealed audit

If the consensus rule passes the six-fold audit:

- fit the final dollar/rates model only through 2025-06-30;
- apply the already frozen consensus threshold to July–September 2025 as an audit-only quarter;
- do not use that quarter to change the threshold, family, multiplier, or any other rule;
- preserve its monthly and aggregate results in the report.

## Sealed evaluation

After all rules are frozen, evaluate the same five exposed windows from 2025-10-01 through 2026-06-30 once under standard and stress costs.

All original profitability, diversity, concentration, action, cost, drawdown, exposure, independent-replication, and current-market-smoke gates remain unchanged.

v5.1 is a final historical breakthrough only if every original historical gate passes. Otherwise v4.4 remains the best verified baseline.

No live trading is authorized.