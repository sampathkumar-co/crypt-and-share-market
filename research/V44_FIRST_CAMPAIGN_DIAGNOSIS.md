# v4.4 First Campaign Diagnosis

## Status

`RETROSPECTIVE_NOT_YET_BREAKTHROUGH`

v4.4 is a verified accounting improvement over v4.3, not a new alpha breakthrough. It correctly credits prior-day-known public 3-month Treasury yield to idle cash while leaving the frozen v4.3 signals, selections, cadence, exposure, and transaction-cost assumptions unchanged.

The result is paper-only, retrospective, and does not authorize live trading.

## Reproducibility record

The campaign was reproduced by GitHub Actions from the v4.4 branch.

- Workflow: `v4.4 Yield-Bearing Cash Evidence`
- Workflow run ID: `30701943106`
- Head commit: `69868609e059b17b349f3067d5311a7bed53e6cd`
- Artifact ID: `8819162950`
- Artifact digest: `sha256:8f1bab299842e7d05d0af4dfc6c712eb41cdeff801eb119eb48b2692c89afcdf`
- Artifact retention: 90 days
- Focused tests: 20 passed
- Repository CI: passed on Python 3.10, 3.11, 3.12, and 3.13

The uploaded artifact contains:

- `evidence/v43/historical.json`
- `evidence/v43/bundle.joblib`
- `evidence/v44/historical.json`

The v4.4 reproducer verified all of the following before applying the cash overlay:

- exact source inventory
- exact dataset metadata
- exact v4.3 bundle summary
- exact v4.3 sealed-window evaluation
- no second v4.3 training pass for the overlay
- unchanged target-changing actions
- unchanged selected assets
- unchanged signal and risk parameters

## Evidence fingerprints

- Reproduced v4.3 report SHA-256: `03c100f2bc88bc63b31fd64996412ee29799c89cb223d244447ca53884f358b2`
- Reproduced v4.4 report SHA-256: `1ee777676b3e53c02f2d04823d31d881288b0e9a2ea3e287329fcc6828cc7c8d`
- Frozen v4.3 bundle SHA-256: recorded inside the v4.4 report

Cash source:

- Provider: `fred-federal-reserve-public-csv`
- Series: `DGS3MO`
- Observations: 1,122
- First observation: 2022-01-03
- Last observation: 2026-06-30
- Raw source SHA-256: `01dbd9ca47cdb7b2e332266d7d8d24e0f5a2d20e7c6d70457b1216ea3db965c1`

Runtime:

- Python 3.13.14
- NumPy 2.5.1
- scikit-learn 1.9.0
- joblib 1.5.3

## Aggregate result

| Metric | v4.3 | v4.4 | Change |
|---|---:|---:|---:|
| Standard return | 0.4287% | 3.0968% | +2.6681 pp |
| Stress return | 0.1069% | 2.7665% | +2.6596 pp |
| Annualized standard return | 0.5909% | 4.2901% | +3.6992 pp |
| Maximum drawdown | 1.1747% | 1.0544% | improved |
| Target-changing actions | 51 | 51 | unchanged |
| Selected universe | 5 assets | 5 assets | unchanged |

Additional v4.4 facts:

- Cash contribution: 2.6275%
- Verification days: 265
- Maximum target exposure: 5% per selected asset
- Maximum positive asset share: 41.99%
- Maximum positive window share: 34.22%
- Maximum positive regime share: 100.00%

## Window results

| Window | Standard | Stress | Cash contribution | Max drawdown | Actions | Selected assets |
|---|---:|---:|---:|---:|---:|---|
| sealed-1 | +0.9468% | +0.9059% | +0.5800% | 0.2512% | 9 | BTC, XRP |
| sealed-2 | +1.0195% | +0.9382% | +0.5392% | 0.3027% | 11 | ADA, BTC, ETH, SOL, XRP |
| sealed-3 | -0.1332% | -0.2026% | +0.5329% | 1.0544% stress | 8 | ADA, ETH, SOL |
| sealed-4 | +1.0946% | +0.9930% | +0.5190% | 0.3370% stress | 18 | BTC, ETH, SOL, XRP |
| sealed-5 | +0.1378% | +0.1080% | +0.4564% | 0.3183% stress | 5 | ETH, SOL |

## Gate result

Passed:

- aggregate stress return is positive
- at least four stress windows are positive
- maximum drawdown remains below the cap
- at least 20 costed target changes occur
- asset diversity requirement
- asset-concentration requirement
- window-concentration requirement

Failed:

- all five standard windows positive: sealed-3 remains negative
- annualized standard return at least 5%: achieved 4.2901%
- regime concentration: all positive strategy contribution is attributed to `chop`
- independent source replication
- current-market smoke test
- untouched historical dates, false by design because v4.4 is retrospective

## Root-cause diagnosis

### 1. Cash accounting was a real omission

The 2.6275% cash contribution is economically material because the model keeps most capital idle. Adding yield lifted annualized standard return from roughly 0.59% to 4.29% without changing a single strategy action. This validates v4.4 as an accounting correction.

### 2. The remaining blocker is alpha quality, not accounting

The strategy still loses before cash yield in sealed-3 and sealed-5. Cash turns sealed-5 positive, but cannot fully rescue sealed-3. Further manipulation of the cash assumption would be inappropriate and would not solve the alpha weakness.

### 3. Sealed-3 is dominated by ADA loss

Sealed-3 standard asset contribution includes:

- ADA: -0.7899%
- ETH: +0.1073%
- SOL: +0.0856%

The negative ADA contribution is much larger than the positive ETH and SOL contributions combined. The next model generation must improve downside discrimination and cross-asset ranking during this environment rather than add an asset-specific retrospective exclusion.

### 4. Regime attribution is structurally concentrated

Aggregate strategy contribution by assigned holding regime is:

- chop: +0.7600%
- trend: 0.0000%
- recovery: 0.0000%
- panic: 0.0000%

This means the current regime layer is not providing diversified decision pathways. A model that effectively trades only one inferred regime cannot satisfy the concentration gate and is unlikely to remain robust across future environments.

### 5. The target is close but must not be chased by gate fitting

The annualized result is approximately 0.71 percentage points below the 5% historical gate. That gap is small enough to motivate another research generation, but sealed dates are already exposed. v4.5 must therefore use nested pre-sealed validation or genuinely future observations for selection; it must not tune directly to sealed-3 until it turns positive.

## Decision

Freeze v4.4 as the accounting baseline. Do not alter:

- cash source or prior-day availability rule
- transaction costs
- exposure limits
- v4.3/v4.4 sealed-window definitions
- profitability acceptance gates
- retrospective labeling

The next research generation should target regime-diversified utility and worst-window robustness. Its success criteria must include:

1. positive contribution from at least two independently assigned non-panic regimes
2. lower downside exposure to a single cross-asset ranking error
3. calibration based on pre-sealed nested folds, not the exposed sealed windows
4. unchanged paper-only and long-or-cash safety boundaries
5. full costed simulation with next-bar execution and no look-ahead

Recommended next branch: `research/v45-regime-diversified-utility`.
