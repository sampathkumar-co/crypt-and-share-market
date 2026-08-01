# v4.6 First Campaign Diagnosis

## Decision

`NO_ROBUST_ACTIVE_VETO_FOUND`

v4.6 completed successfully and behaved safely. Its six-fold walk-forward selection rejected every veto configuration that actually changed a baseline decision. The selected configuration was the mandatory all-disabled fallback, so final sealed performance exactly reproduced v4.4.

This is a valid negative research result. v4.6 must not be described as an alpha improvement.

## Reproducibility

- Workflow run ID: `30703189923`
- Validated head commit: `cf64425bdd10e839f1d16afb5cc461597a6bf93a`
- Focused tests: 36 passed
- Full repository CI: passed on Python 3.10, 3.11, 3.12, and 3.13
- Artifact ID: `8819578806`
- Artifact digest: `sha256:0f1f2e9b252a44ece4e5c25b3490a53cfba5bdd508212dab79e298173532d0d2`
- v4.6 report SHA-256: `0af9e0c3172b6c01644fa4112deff9d9b40f79871afbb8d6410b308be2350e75`

The artifact contains the frozen v4.3 report, frozen v4.3 bundle, and complete v4.6 report with all 72 candidate summaries and six selected-fold records.

## Selected configuration

The selected configuration was the exact disabled baseline:

- q20 floor: disabled
- cross-regime dispersion veto: disabled
- worst cross-sectional q20 veto: disabled
- minimum utility margin: 0.000

`selected_is_disabled_baseline = true`

No asset was vetoed in the final sealed evaluation.

## Candidate selection result

- Total veto configurations: 72
- Eligible configurations: 4
- Ineligible configurations: 68
- Every ineligible configuration failed because at least one fold had negative excess return versus its independently trained baseline.

The four eligible configurations were:

1. all vetoes disabled;
2. q20 floor -0.03 only;
3. q20 floor -0.02 only; and
4. q20 floor -0.01 only.

All four eligible configurations were behaviorally identical:

- zero veto interventions;
- zero excess return in every fold;
- zero positive-excess folds; and
- unchanged actions and turnover.

The closest active family used a minimum utility margin of 0.002 or 0.004 without the other vetoes. It improved only one fold and had a worst-fold excess return of -0.1116 percentage points. Worst-q20 veto families had a best worst-fold excess of -0.2513 percentage points. Dispersion-based families were substantially less stable, with worst-fold excess losses around 3.05 to 3.26 percentage points.

## Walk-forward baseline behavior

| Fold | Validation period | Baseline return | Actions | Max drawdown |
|---|---|---:|---:|---:|
| WF-1 | 2024-04-01 to 2024-06-30 | +1.2840% | 2 | 0.0315% |
| WF-2 | 2024-07-01 to 2024-09-30 | +1.0846% | 23 | 0.6067% |
| WF-3 | 2024-10-01 to 2024-12-31 | +4.1883% | 24 | 0.3205% |
| WF-4 | 2025-01-01 to 2025-03-31 | +1.5931% | 17 | 0.6745% |
| WF-5 | 2025-04-01 to 2025-06-30 | -0.0194% | 10 | 0.6983% |
| WF-6 | 2025-07-01 to 2025-09-30 | +0.4226% | 4 | 0.5186% |

The hard-routing base family itself was positive in five of six pseudo-out-of-sample quarters. This supports retaining v4.4 as the current learned baseline, but it does not justify any tested veto.

## Final sealed result

Because the disabled baseline was selected, v4.6 exactly reproduced v4.4:

- standard return: +3.0968%
- stress return: +2.7665%
- annualized standard return: 4.2901%
- maximum drawdown: 1.0544%
- target-changing actions: 51
- vetoed assets: none
- status: `RETROSPECTIVE_NOT_YET_BREAKTHROUGH`

Standard sealed-window returns:

1. sealed-1: +0.9468%
2. sealed-2: +1.0195%
3. sealed-3: -0.1332%
4. sealed-4: +1.0946%
5. sealed-5: +0.1378%

The same historical gates remain unsatisfied:

- all five standard windows positive;
- annualized standard return at least 5%;
- regime concentration;
- independent replication;
- current-market smoke; and
- untouched historical dates, false by retrospective design.

## Interpretation

### 1. The fail-safe worked

Unlike v4.5, v4.6 did not force an intervention merely because one looked attractive in a short calibration period. Its minimum-fold eligibility rule correctly rejected every active veto.

### 2. Decision-layer suppression is not the missing alpha

q20, utility-margin, worst-cross-sectional-q20, and cross-regime-dispersion vetoes did not deliver non-negative excess return across all six folds. Continuing to tune these veto thresholds against exposed evidence would be overfitting.

### 3. The remaining problem requires a distinct return source

v4.4 already has conservative exposure, low drawdown, and mostly positive windows. Its remaining gap cannot be responsibly closed by accounting changes, routing mixtures, or trade suppression. The next generation must add a separately motivated alpha family with its own walk-forward evidence.

## Next research direction

Freeze v4.6 as proof that the tested risk overlays do not improve v4.4 robustly.

The next generation should test a sparse, rule-transparent, cross-asset residual-momentum family that is structurally independent of the v4.3 learned utility model:

1. rank assets by medium-horizon return after removing BTC beta;
2. require multi-timeframe trend agreement and volatility-normalized positive residual momentum;
3. use volatility-compression breakout confirmation where available;
4. keep long-or-cash, 5% per-asset exposure, and completed-candle/next-bar execution;
5. select thresholds only through multiple walk-forward folds;
6. compare the family standalone and as a low-correlation complement to v4.4;
7. reject it unless it is positive across a broad majority of folds and improves the worst combined fold without increasing concentration.

Recommended next branch: `research/v47-residual-momentum-breakout`.
