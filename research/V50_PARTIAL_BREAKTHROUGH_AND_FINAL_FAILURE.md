# v5.0 Partial Breakthrough and Final Failure

## Verdict

v5.0 produced the first active macro controller in this research sequence to pass every predeclared six-fold walk-forward selection rule. The selected family was `fresh_14d`.

That is a genuine mechanism-level result: the active controller had positive compounded validation excess, four strictly positive folds, no negative fold, no action or turnover increase, no drawdown violation, and causal downside-only exposure.

However, v5.0 did **not** produce a final historical profitability breakthrough. After the family was frozen and calibrated on July–September 2025, its exposed sealed evaluation underperformed v4.4 and failed the original profitability gates. It therefore remains research-only and does not replace v4.4.

## Authoritative evidence

- Pull request: `#97`
- Workflow run: `30709957390`
- Job: `91395547212`
- Head commit evaluated: `4b370a694205e4257b0d939a8989476e8219c4e4`
- Focused tests: `73 passed`
- Evidence validation: passed
- Report SHA-256: `8e9affe745095b3a654a9f2ee18003085beb1c7a372ebd19de2e5b3d9f958dd0`
- Artifact: `v50-fresh-transition-30709957390`
- Artifact ID: `8821650937`
- Artifact digest: `sha256:8774bf175ec2da56e4dbe274130ad22cb314bfb563a879c01ee9ef9b42cfe77b`

## Walk-forward family selection

| Family | Eligible | Minimum fold excess | Positive folds | Compounded excess | Attenuated decisions | Verdict |
|---|:---:|---:|---:|---:|---:|---|
| `fresh_3d` | No | `-0.0011629437019879951` | 0/6 | `-0.0019439961641528614` | 2 | rejected |
| `fresh_7d` | No | `-0.0008609170008480049` | 1/6 | `0.0004312660998193607` | 7 | insufficient consistency |
| `fresh_14d` | **Yes** | `0.0` | **4/6** | **0.0033503475851033304** | 13 | selected |

### Selected `fresh_14d` folds

| Fold | Threshold | Validation excess | Attenuated decisions | Crossings | Active dates |
|---|---:|---:|---:|---:|---:|
| WF-1 | `0.60` | `0.00025947595841269155` | 1 | 4 | 48 |
| WF-2 | `0.55` | `0.0004453652650617812` | 9 | 4 | 40 |
| WF-3 | `0.65` | `0.0` | 0 | 0 | 0 |
| WF-4 | `0.65` | `0.0` | 0 | 0 | 0 |
| WF-5 | `0.60` | `0.000006916900424425165` | 2 | 1 | 14 |
| WF-6 | `0.50` | `0.002655854486571352` | 1 | 5 | 51 |

The selected family passed all frozen eligibility rules. Its selection key was:

`[0.0, 4, 0.0033503475851033304, -0.00018719899510821758, -0.006982772900776535, -3.8005966091145162, -80, -13, -14, "fresh_14d"]`

## Final calibration

Using only July–September 2025, the final rule selected:

- family: `fresh_14d`
- threshold: `0.50`
- multiplier: `0.50`
- training date count: `986`
- calibration minimum monthly excess: `0.0`
- calibration compounded excess: `0.0032699258694606215`
- calibration attenuated decisions: `4`

Monthly calibration excess:

- July 2025: `0.0023450203035071393`
- August 2025: `0.0`
- September 2025: `0.000932697790827719`

## Final sealed evaluation

- aggregate standard return: `0.027738928925329143`
- aggregate stress return: `0.02480956973854065`
- annualized standard return: `0.038405274219430074`
- maximum drawdown: `0.01054364964009935`
- attenuated decisions: `10`
- crossings: `12`
- active transition dates: `94`
- target-changing actions: `51`
- maximum target exposure: `0.05`
- maximum selected cardinality: `1`
- status: `RETROSPECTIVE_NOT_YET_BREAKTHROUGH`

Standard sealed-window returns:

1. `0.00879922940398381`
2. `0.006566115670290973`
3. `-0.0013321789942171147`
4. `0.010443292663140147`
5. `0.0030042127347402747`

Compared with v4.4:

- standard-return uplift: `-0.003228761057395557`
- stress-return uplift: `-0.0028551711203232966`
- annualized-return uplift: `-0.0044959778481898915`

The controller improved sealed window 5, left sealed window 3 unchanged, and reduced returns in sealed windows 1, 2, and 4.

## Gate result

Passed:

- positive aggregate stress return;
- drawdown cap;
- asset diversity and concentration;
- window concentration;
- at least four positive stress windows;
- at least twenty costed actions;
- all exposure and no-added-asset safety invariants.

Failed:

- annualized standard return of at least 5%;
- five positive standard windows;
- regime concentration;
- independent-source replication;
- current-market smoke;
- untouched historical dates.

## What was learned

The duration hypothesis was correct at the walk-forward level. Limiting attenuation to a fresh 14-day transition window removed the prolonged-state failure that defeated v4.8 and v4.9.

The remaining problem is not basic causal robustness. It is calibration stability and economic usefulness after the walk-forward stage:

- walk-forward thresholds were `0.60, 0.55, 0.65, 0.65, 0.60, 0.50`;
- the final quarter selected the lowest observed threshold, `0.50`;
- the active family survived all fold gates, but the final calibrated rule attenuated ten sealed decisions and reduced total return.

This does not justify changing the threshold after seeing sealed outcomes. The sealed result must not be used to cherry-pick `0.55`, `0.60`, or `0.65`.

## Next research boundary

A defensible next experiment is calibration-stability testing that freezes the transition threshold using a predeclared consensus procedure derived only from the six walk-forward calibration thresholds, such as the median, rather than recalibrating on the final quarter.

The procedure—not its resulting numeric threshold—must be frozen before another exposed retrospective evaluation. The family should remain `fresh_14d`, the multiplier must remain `0.50`, and all original gates and safety rules must remain unchanged.

v4.4 remains the best verified final baseline. No live trading is authorized.