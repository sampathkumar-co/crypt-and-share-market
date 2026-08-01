# v4.8 Negative or Null Result

## Verdict

v4.8 did not produce a historical breakthrough. The protocol-correct selection chose the disabled multiplier `1.0`, so the final sealed evaluation reproduced v4.4 exactly.

This is a valid null result, not a failed run. The dedicated workflow completed successfully, all focused tests passed, the frozen baseline reproduced exactly, and the safety/evidence validator accepted the report.

## Authoritative evidence

- Pull request: `#95`
- Workflow run: `30707936784`
- Job: `91390185212`
- Head commit: `e000ebe9caed3909ecad1c46ceeaa30ba3b7d536`
- Focused tests: `50 passed`
- Report SHA-256: `6031914e9c8057105eaf976b87ead0a807e956618bc75672f9e3f8292153cb51`
- Artifact: `v48-dollar-rates-30707936784`
- Artifact ID: `8821049070`
- Artifact digest: `sha256:5f41130cc401557633f0a676c0bc4fa1262742b85754f15e1c9d45eb63c4ad9f`

## Final sealed result

- selected multiplier: `1.0`
- selected threshold: `null`
- attenuated sealed decisions: `0`
- aggregate standard return: `0.0309676899827247`
- aggregate stress return: `0.027664740858863945`
- annualized standard return: `0.042901252067619966`
- maximum drawdown: `0.01054364964009935`
- status: `RETROSPECTIVE_NOT_YET_BREAKTHROUGH`

Standard sealed-window returns remained:

1. `0.009467781764318728`
2. `0.01019534836961955`
3. `-0.0013321789942171147`
4. `0.010946400945364143`
5. `0.001377989360188625`

## Walk-forward multiplier diagnosis

All active multipliers failed only the predeclared requirement for at least four strictly positive validation folds.

| Multiplier | Eligible | Minimum fold excess | Positive folds | Compounded excess | Attenuated decisions | Ineligibility reason |
|---:|:---:|---:|---:|---:|---:|---|
| 0.25 | No | -0.002023946892612649 | 2/6 | 0.010849657371440058 | 20 | fewer than four positive excess folds |
| 0.50 | No | -0.0013449544577446293 | 2/6 | 0.007231300630682114 | 20 | fewer than four positive excess folds |
| 0.75 | No | -0.0006703026048566763 | 2/6 | 0.003614729579927367 | 20 | fewer than four positive excess folds |

Per-fold excess for multiplier `0.25`:

- WF-1, 2024-04-01 through 2024-06-30: `0.0`, no attenuation
- WF-2, 2024-07-01 through 2024-09-30: `-0.002023946892612649`, 12 attenuated decisions
- WF-3, 2024-10-01 through 2024-12-31: `0.0`, no attenuation
- WF-4, 2025-01-01 through 2025-03-31: `0.0`, no attenuation
- WF-5, 2025-04-01 through 2025-06-30: `0.008088576762277033`, 6 attenuated decisions
- WF-6, 2025-07-01 through 2025-09-30: `0.004769174151945554`, 2 attenuated decisions

The same activation pattern occurred for all three multipliers. Thresholds were `0.35, 0.55, 0.35, 0.35, 0.50, 0.50` across WF-1 through WF-6.

## What was learned

The dollar/rates family is not robust enough as an absolute low-state probability controller. It was inactive in half of the validation folds, harmful in one active fold, and beneficial in the final two active folds.

However, the signal is not empty:

- every active multiplier had positive compounded validation excess;
- the strongest attenuation produced the largest compounded excess;
- actions never increased;
- turnover fell materially;
- drawdown never worsened beyond the allowance;
- the only failed gate was cross-fold consistency.

This pattern suggests that the useful information may be concentrated in abrupt joint dollar/rate deterioration episodes rather than in an absolute classifier-probability level.

## Next research boundary

A subsequent version must not relax the v4.8 eligibility rules or tune against the exposed sealed windows.

The next defensible hypothesis is a predeclared dollar/rates shock detector evaluated only through the same six walk-forward folds. It should test changes or acceleration in the macro state, not another wider search over absolute probability thresholds. The disabled v4.4 baseline must remain the fallback, and the sealed windows must remain untouched until the new rule is frozen.

No live trading is authorized.