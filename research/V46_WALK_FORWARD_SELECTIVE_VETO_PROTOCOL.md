# v4.6 Walk-Forward Selective Risk Veto Protocol

## Purpose

v4.6 responds to the rejected v4.5 full soft-routing campaign. It keeps the verified v4.4/v4.3 hard router as the default decision engine and tests whether a narrowly scoped risk layer can suppress unreliable baseline trades without creating new exposure.

## Safety boundary

- Paper-only and long-or-cash only.
- No live credentials, order placement, or exchange execution.
- Completed daily candles only.
- Existing next-bar economic return labels.
- Frozen standard and stress transaction costs.
- Frozen v4.4 prior-day-known `DGS3MO` cash accounting.
- Frozen 5% per-asset target.
- The veto may only remove a baseline-selected asset and replace it with cash.
- The veto may not introduce another asset, increase `top_n`, increase target exposure, or trade off cadence.

## Walk-forward selection boundary

The veto configuration must be selected from six pseudo-out-of-sample validation folds. Each fold independently refits and calibrates the v4.3 base model before its validation quarter.

| Fold | Base training end | Base calibration | Veto validation |
|---|---|---|---|
| WF-1 | 2023-12-31 | 2024-01-01 to 2024-03-31 | 2024-04-01 to 2024-06-30 |
| WF-2 | 2024-03-31 | 2024-04-01 to 2024-06-30 | 2024-07-01 to 2024-09-30 |
| WF-3 | 2024-06-30 | 2024-07-01 to 2024-09-30 | 2024-10-01 to 2024-12-31 |
| WF-4 | 2024-09-30 | 2024-10-01 to 2024-12-31 | 2025-01-01 to 2025-03-31 |
| WF-5 | 2024-12-31 | 2025-01-01 to 2025-03-31 | 2025-04-01 to 2025-06-30 |
| WF-6 | 2025-03-31 | 2025-04-01 to 2025-06-30 | 2025-07-01 to 2025-09-30 |

Each fold must select its base v4.3 model and hard-routing thresholds using only its training and base-calibration periods. The validation quarter must not influence the base bundle or veto configuration for that fold.

The exposed v4.3/v4.4/v4.5 sealed windows remain final retrospective reporting only.

## Veto metrics

For each baseline-selected asset on a scheduled decision date, compute only completed-date model outputs:

- selected specialist q20 prediction;
- selected specialist recency-member disagreement;
- probability-weighted cross-regime utility dispersion;
- cross-sectional q20 rank among the five assets; and
- panic probability.

Panic handling remains the baseline behavior and is not overridden.

## Allowed veto grid

- q20 veto floor: disabled, -0.03, -0.02, -0.01
- cross-regime dispersion veto: disabled, calibration quantile 0.75, calibration quantile 0.90
- worst-q20 cross-sectional veto: disabled or enabled
- minimum baseline utility margin above its frozen threshold: 0.000, 0.002, 0.004

The all-disabled configuration is mandatory and represents exact v4.4-equivalent baseline routing.

## Selection objective

For each veto configuration, simulate baseline and veto behavior independently on every validation fold using v4.4 cash accounting and standard cost.

A non-baseline veto configuration is eligible only when:

1. its minimum fold excess return versus baseline is non-negative within numerical tolerance;
2. it does not increase aggregate target-changing actions;
3. it does not increase aggregate turnover; and
4. it does not increase maximum drawdown by more than 0.25 percentage points in any fold.

Eligible configurations are selected lexicographically by:

1. highest minimum fold excess return;
2. highest count of positive excess-return folds;
3. highest compounded excess return;
4. highest worst absolute fold return;
5. lowest maximum fold drawdown;
6. lowest aggregate turnover;
7. lowest aggregate action count;
8. least intervention in deterministic order.

If no non-baseline configuration is eligible, select the all-disabled baseline. This fail-safe is an expected valid result, not an error.

## Final retrospective evaluation

Apply the frozen veto configuration to the supplied final v4.3 bundle and evaluate the unchanged five sealed windows with v4.4 cash accounting.

Report:

- all walk-forward base and veto fold summaries;
- selected veto configuration and eligibility diagnostics;
- exact difference versus v4.4 sealed behavior;
- per-window standard/stress return and actions;
- asset and regime contribution;
- all existing historical gates;
- source, protocol, contract, implementation, bundle, and runtime fingerprints.

## Interpretation

A v4.6 historical candidate must pass all existing historical gates except independent replication, current-market smoke, and untouched historical dates. Regardless of result, it does not authorize trading.
