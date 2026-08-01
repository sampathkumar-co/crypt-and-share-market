# v4.6 Walk-Forward Selective Veto — Result and Next Step

## Verified result

GitHub Actions run `30703189923` completed successfully and uploaded the full v4.6 evidence bundle.

The campaign:

- reproduced the frozen v4.3 report and bundle;
- refit and calibrated six independent walk-forward base models;
- evaluated 72 selective-veto configurations;
- required non-negative excess return versus baseline in every validation fold;
- rejected any configuration that increased actions, turnover, or drawdown beyond the allowance;
- preserved prior-day-known DGS3MO cash yield and the paper-only boundary.

## Selection outcome

Four configurations were technically eligible, but the strongest robust selection was the disabled veto:

```json
{
  "dispersion_quantile": null,
  "minimum_utility_margin": 0.0,
  "q20_floor": null,
  "veto_worst_q20": false
}
```

All six selected-fold excess returns were exactly `0.0`. No asset was vetoed in sealed evaluation.

## Sealed result

- aggregate standard return: `0.0309676899827247`
- aggregate stress return: `0.027664740858863945`
- annualized standard return: `0.042901252067619966`
- maximum drawdown: `0.01054364964009935`
- standard sealed windows:
  - `0.009467781764318728`
  - `0.01019534836961955`
  - `-0.0013321789942171147`
  - `0.010946400945364143`
  - `0.001377989360188625`
- status: `RETROSPECTIVE_NOT_YET_BREAKTHROUGH`
- report SHA-256: `0af9e0c3172b6c01644fa4112deff9d9b40f79871afbb8d6410b308be2350e75`

## Interpretation

v4.6 is a successful safety result and a negative alpha result.

The walk-forward framework correctly refused to adopt a crypto-native risk veto that could not demonstrate improvement across independent historical folds. This confirms that further threshold tuning over the existing funding, basis, open-interest, flow, volatility, breadth, and disagreement features is unlikely to solve the remaining gate without overfitting.

## Next step

v4.7 must introduce a genuinely independent information family rather than another transformation of the same crypto-native features.

The next candidate is a prior-day-known macro-liquidity state built from public daily FRED series:

- `VIXCLS` — equity volatility;
- `DTWEXBGS` — broad U.S. dollar index;
- `DGS10` — 10-year U.S. Treasury yield;
- `NASDAQCOM` — technology/risk-asset appetite.

The macro layer will be evaluated with walk-forward training and will default to exact v4.4 behavior unless it demonstrates robust positive excess without increasing exposure or trading frequency.
