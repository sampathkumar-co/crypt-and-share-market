# v4.6 Walk-Forward Selective Veto — Result Diagnosis

## Verdict

v4.6 completed successfully but did not discover a robust active veto.

The selected configuration was the disabled baseline:

```json
{
  "q20_floor": null,
  "dispersion_quantile": null,
  "veto_worst_q20": false,
  "minimum_utility_margin": 0.0
}
```

No assets were vetoed. The sealed result therefore reproduced v4.4 exactly:

- aggregate standard return: `0.0309676899827247`
- aggregate stress return: `0.027664740858863945`
- annualized standard return: `0.042901252067619966`
- maximum drawdown: `0.01054364964009935`
- status: `RETROSPECTIVE_NOT_YET_BREAKTHROUGH`

## Walk-forward evidence

- six independent base-model refits and following-quarter validations completed
- 72 veto configurations were evaluated
- four configurations were eligible, but all four were behaviorally identical no-op configurations
- selected fold excess returns were exactly zero in all six folds
- no active veto demonstrated non-negative excess in every fold without increasing actions or turnover

## Interpretation

The remaining blocker is not solved by thresholding the same v4.3 distributional outputs more aggressively. The veto search correctly refused to turn isolated historical weaknesses into a fitted rule.

The next experiment must introduce genuinely independent predictive information. It must not tune another q20, disagreement, dispersion, or utility threshold around the same feature family.

## Frozen next step

v4.7 should test an independently sourced macro-risk/liquidity signal with publication-lag-safe alignment. The existing v4.4 strategy remains the control, and v4.7 must be selected using walk-forward periods before the exposed sealed campaign.

Paper-only. No live-trading authorization.
