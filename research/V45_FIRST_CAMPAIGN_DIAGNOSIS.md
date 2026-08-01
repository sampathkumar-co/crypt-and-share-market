# v4.5 First Campaign Diagnosis

## Decision

`REJECT_FULL_SOFT_ROUTING`

The first v4.5 campaign is technically valid and fully reproducible, but its research result is materially worse than v4.4. The full soft specialist-routing approach must not replace the frozen v4.4 baseline.

## Reproducibility

- Workflow run ID: `30702593048`
- Head commit: `a26bc867820ae4a86867317317c043d00f1c99f4`
- Focused tests: 27 passed
- Full repository CI: passed on Python 3.10, 3.11, 3.12, and 3.13
- Artifact ID: `8819350534`
- Artifact digest: `sha256:ffe0a82d831afd9c375424dad9aa88ec1e51dd1ecda8b0fc036df03c2bf76171`
- v4.5 report SHA-256: `06b41320ea9dc18ae91bedbfe8378e9d95d62d4ea7e9d13a6bba3fc4fb3b4694`

## Selected pre-sealed configuration

The three July–September 2025 calibration blocks were all positive. The selected configuration was:

- panic threshold: 0.65
- utility threshold: 0.006
- q20 floor: -0.02
- top_n: 2
- downside exclusion count: 0
- entropy penalty: 0.0
- cross-regime dispersion penalty: 0.25
- dispersion quantile: 0.90
- dispersion threshold: 0.0309875943470563

Calibration results:

- worst blocked return: +0.8576%
- positive blocked periods: 3 of 3
- compounded blocked return: +3.3225%

This apparently strong calibration did not transfer to the sealed period.

## Sealed result

| Metric | v4.4 | v4.5 first campaign | Change |
|---|---:|---:|---:|
| Standard return | +3.0968% | -3.3561% | -6.4528 pp |
| Stress return | +2.7665% | -4.0759% | -6.8424 pp |
| Annualized standard | +4.2901% | -4.5930% | -8.8831 pp |
| Maximum drawdown | 1.0544% | 4.3067% | worse |
| Target-changing actions | 51 | 79 | +28 |
| Maximum positive regime share | 100% | 100% | unchanged failure |

Standard sealed-window returns:

1. sealed-1: -0.5647%
2. sealed-2: +0.0497%
3. sealed-3: -1.8275%
4. sealed-4: +1.0033%
5. sealed-5: -2.0300%

## Failure mechanism

### 1. Full soft routing changed too much behavior

The model increased target-changing actions from 51 to 79 and selected up to two assets instead of the v4.4 single-asset behavior. That increased exposure to ranking error and transaction costs.

### 2. Short calibration was not representative

The only truly out-of-sample configuration period available after the frozen v4.3 training end was July–September 2025. All three monthly blocks favored the aggressive soft router, but the following nine months behaved differently. Three adjacent months were insufficient to establish regime robustness.

### 3. The downside veto was not selected

The calibration selected `downside_exclusion_count = 0`, so the mechanism intended to reduce a single severe ranking error was inactive. This is evidence that optimizing the veto inside the short adjacent calibration period is unreliable.

### 4. Regime diversification did not materialize

Both chop and trend attribution lost money in the sealed period, and the positive-contribution concentration metric remained 100%. Soft mixing changed attribution labels and trades but did not create robust independent alpha pathways.

### 5. Losses broadened beyond ADA

Aggregate asset contribution was:

- ADA: -0.5360%
- BTC: -2.1536%
- ETH: +1.2388%
- SOL: -4.2659%
- XRP: +0.6189%

The first campaign did not merely fail to remove the prior ADA problem; it introduced large BTC and SOL losses through broader exposure.

## What must change next

Do not tune the first v4.5 grid against the now-exposed sealed result.

The next research generation must:

1. use multiple walk-forward pseudo-out-of-sample folds with model refitting at each historical cutoff;
2. keep v4.4 hard routing as the default behavior;
3. allow a learned layer only to veto or reduce a baseline trade, not broadly create extra positions;
4. preserve `top_n = 1` and the 5% target exposure in the first campaign;
5. require non-negative excess return versus the v4.4 routing baseline across the walk-forward folds;
6. penalize additional actions and turnover directly;
7. evaluate the sealed period only after the selective overlay is frozen.

Recommended next generation: `v4.6 walk-forward selective risk veto`.
