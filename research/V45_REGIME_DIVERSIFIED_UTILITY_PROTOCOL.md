# v4.5 Regime-Diversified Utility Protocol

## Purpose

v4.5 is a paper-only alpha research generation built on the verified v4.4 yield-bearing-cash accounting baseline. It targets two diagnosed weaknesses:

1. all positive v4.4 strategy contribution was attributed to one inferred regime (`chop`); and
2. one cross-asset ranking error, dominated by ADA in sealed-3, was large enough to keep a verification window negative.

v4.5 must improve regime participation and worst-window robustness without modifying the v4.4 cash source, cash timing, transaction costs, exposure limits, historical gates, or sealed-window definitions.

## Safety boundary

- Paper-only.
- Long-or-cash only.
- No exchange credentials, order endpoints, or live execution.
- Completed daily candles only.
- Decision on completed day D; economic return is the existing next-bar return label.
- Maximum target allocation remains 5% per selected asset.
- Maximum two selected assets, subject to calibration.
- Standard and stress transaction costs remain frozen.
- Idle cash uses the v4.4 prior-day-known `DGS3MO` rule.

## Frozen evidence boundary

The five v4.3/v4.4 sealed windows are already exposed and may only be used for final retrospective reporting.

No v4.5 parameter, threshold, model choice, feature choice, or tie-break may be selected from sealed-window results.

All v4.5 selection must use:

- training data ending 2025-06-30; and
- three blocked pre-sealed calibration periods:
  - calibration-A: 2025-07-01 through 2025-07-31
  - calibration-B: 2025-08-01 through 2025-08-31
  - calibration-C: 2025-09-01 through 2025-09-30

The calibration objective must reward the worst blocked return, not only aggregate return.

## Model family

v4.5 retains the v4.3 recency ensemble and regime-specialist regressors, then replaces hard single-regime routing with an agreement-gated soft mixture.

For each completed date:

1. Average regime probabilities across assets and recency classifiers.
2. If panic probability crosses the calibrated threshold, hold cash.
3. For every asset and every available non-panic specialist, compute the frozen v4.3 component metrics.
4. Convert each specialist output into a specialist utility using the frozen v4.3 utility formula.
5. Form a probability-weighted mixture across available specialists.
6. Penalize:
   - disagreement among recency members;
   - disagreement among regime-specialist utilities; and
   - high regime-probability entropy.
7. Apply a cross-sectional downside veto using the mixed q20 estimate. The calibrated veto may exclude the lowest-downside-ranked asset or may be disabled.
8. Rank surviving assets by mixed rank, then mixed utility, then asset symbol.

The holding attribution regime is the non-panic specialist with the largest positive probability-weighted utility contribution for that asset on the decision date. This attribution is fixed when the position is opened and is used only for concentration diagnostics.

## Calibration grid

The implementation may calibrate only the following decision parameters:

- panic threshold: 0.45, 0.55, 0.65
- mixed utility threshold: 0.002, 0.004, 0.006, 0.008
- q20 floor: -0.03, -0.02, -0.01
- maximum cross-regime utility dispersion: calibration quantile 0.75 or 0.90
- downside exclusion count: 0 or 1 asset per date
- top_n: 1 or 2
- regime entropy penalty: 0.00, 0.0025, 0.0050
- cross-regime dispersion penalty: 0.25, 0.50, 0.75

No other search dimensions are allowed in the first v4.5 campaign.

## Calibration objective

For each candidate configuration, simulate calibration-A, calibration-B, and calibration-C independently at standard cost.

The primary score is lexicographic:

1. highest worst blocked net return;
2. highest count of positive blocked periods;
3. highest compounded blocked return;
4. lowest maximum drawdown;
5. lowest turnover;
6. highest number of positive attributed regimes;
7. lowest maximum positive regime share;
8. deterministic conservative parameter tie-break.

A candidate with fewer than six costed target-changing actions across the three blocks receives a hard score penalty.

## Retrospective verification

After calibration is frozen, evaluate the unchanged five sealed windows at standard and stress cost with v4.4 cash accounting.

Report:

- all v4.4 gate metrics;
- per-window standard and stress results;
- cash contribution;
- asset contribution;
- attributed-regime contribution;
- action and selection differences from v4.4;
- calibration-block results and chosen configuration;
- source, model, protocol, contract, and runtime fingerprints.

## Interpretation

A first v4.5 historical success requires all existing historical gates excluding only:

- independent source replication;
- current-market smoke; and
- untouched historical dates, which remains false for this retrospective generation.

Even if those historical gates pass, v4.5 does not authorize trading. It must proceed to independent replication and genuinely future paper observation.
