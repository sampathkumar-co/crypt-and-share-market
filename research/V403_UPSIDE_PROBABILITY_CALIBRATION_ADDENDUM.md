# v4.0.3 Upside-Probability Calibration Addendum

Status: frozen after measuring the two-hour target distribution and before any upside-classifier outcome is calculated.

## Diagnosis

The two-hour dataset contains meaningful tail opportunities: approximately 19% of asset observations exceed the frozen 40-basis-point round-trip stress cost. However, the mean-return regressors shrink predictions toward the near-zero unconditional average and therefore reject every candidate.

This is a known mismatch between noisy conditional-return estimation and opportunity detection. Costs and downside limits are not relaxed.

## Frozen learned component

Add an upside classifier ensemble with target:

`future absolute two-hour return > 0.004`

- Model family: the same histogram gradient boosting classifier family.
- Ensemble seeds are fixed and distinct from the downside ensemble.
- Training, calibration, and untouched test chronology remain unchanged.
- The classifier uses exactly the frozen v4.0 features.

## Training-only probability-to-return calibration

Using training rows only, calculate:

- mean future absolute return when the upside target is true;
- mean future absolute return when the upside target is false.

For probability `p`, calculate:

`classifier_expected_return = p * positive_mean + (1 - p) * nonpositive_mean`

The final expected absolute return is frozen as:

`0.5 * regression_absolute_return + 0.5 * classifier_expected_return`

No untouched-test return may influence these means.

## Calibration threshold

The opportunity-probability threshold is selected using calibration data only from the fixed set:

- 0.25;
- 0.35;
- 0.45.

All existing calibration penalties, stress costs, uncertainty rejection, panic rejection, and downside probability limit remain in force.

## Audit fields

Reports must include:

- upside probability;
- training positive and nonpositive conditional means;
- regression absolute return;
- classifier-implied expected return;
- blended expected absolute return;
- selected opportunity-probability threshold.

## Non-outcome tuning boundary

- The approximately 19% base rate motivates classification but does not choose the calibration threshold.
- No cost, exposure, asset, feature, downside, uncertainty, or test gate changes.
- A ten-minute live result remains diagnostic only.

All original paper-only and isolation requirements remain unchanged.
