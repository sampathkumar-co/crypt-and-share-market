# v4.7 Macro-Liquidity State — Result and Next Step

## Verified result

GitHub Actions run `30706203025` completed successfully after the legacy v4.4 summary compatibility boundary was added and regression-tested.

The workflow:

- compiled the v4.3-v4.7 research modules;
- passed 41 focused tests;
- reproduced the final frozen v4.3 report and bundle;
- downloaded and hashed VIXCLS, DTWEXBGS, DGS10, and NASDAQCOM from fixed public FRED CSV ranges;
- built prior-day-known macro features;
- refit six independent v4.3 walk-forward base models;
- calibrated thresholds without validation or sealed-window data;
- evaluated three macro families;
- validated the paper-only and exposure boundaries;
- uploaded the complete v4.3 bundle/report and v4.7 report.

## Family selection

The robust selector chose the disabled macro gate.

| Family | Compounded validation excess | Positive folds | Worst fold | Gated decisions | Result |
| --- | ---: | ---: | ---: | ---: | --- |
| disabled | 0.0000% | 0/6 | 0.0000% | 0 | selected fallback |
| dollar_rates | +1.0228% | 1/6 | -0.0565% | 54 | failed positive-fold requirement |
| full_macro | -2.1625% | 3/6 | -3.2276% | 46 | rejected |
| risk_appetite | -2.4059% | 1/6 | -2.0956% | 33 | rejected |

The dollar/rates family was the only active family with positive compounded validation excess and a small worst-fold loss, but its binary gate was too sparse and inconsistent to satisfy the predeclared four-positive-fold requirement.

## Sealed result

Because the disabled family won, v4.7 exactly reproduced v4.4:

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
- gated decisions: `0`
- status: `RETROSPECTIVE_NOT_YET_BREAKTHROUGH`
- report SHA-256: `99215db2c72c1792c771972a4efe290dd791d0be3764fd98675fc0ca7aeebdaf`
- artifact ID: `8820583841`
- artifact SHA-256: `ec83de26dca04e352751ba9cad5271f370e5eb3d5c4a4726a50d6cf28e600d55`

## Interpretation

v4.7 is a successful safety result and a negative binary-gating result.

Risk-appetite and full-macro mixtures generalized poorly. Dollar/rates information showed a positive aggregate signal with very limited worst-fold damage, but forcing the model completely to cash when probability fell below a threshold was too blunt: it concentrated benefit in one fold and produced zero or slightly negative excess in the others.

## Next step: v4.8

v4.8 will keep only the independently sourced dollar/rates family and test a strictly downside-only exposure attenuation layer.

The baseline-selected asset identity, rebalance cadence, costs, and maximum exposure remain frozen. When the prior-day-known dollar/rates probability is weak, the layer may reduce the baseline 5% per-asset target to a predeclared fraction such as 25%, 50%, or 75% of that target. It may never raise exposure above baseline or substitute assets.

Attenuation strength will be selected across the same six walk-forward validation folds, while each fold's probability threshold remains calibrated only on its preceding calibration quarter. The disabled v4.4 baseline remains the mandatory fallback.
