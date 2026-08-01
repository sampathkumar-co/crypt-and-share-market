# v4.9 Negative or Null Result

## Verdict

v4.9 did not produce a historical breakthrough. The protocol-correct family selector chose the disabled v4.4 baseline.

The dedicated workflow completed successfully, all 61 focused tests passed, the frozen v4.3/v4.4 baseline reproduced exactly, the evidence validator passed, and the artifact was uploaded. This is a clean null result.

## Authoritative evidence

- Pull request: `#96`
- Workflow run: `30708986550`
- Job: `91392958468`
- Head commit evaluated: `497ecde46ebda1d322d591e0e0c5af7088e9ef66`
- Focused tests: `61 passed`
- Report SHA-256: `c6c8eecf73cb6b49a5e43a9e00fca631cf3e218fd10b060ec491683d4dc10ee4`
- Artifact: `v49-dollar-rates-shock-30708986550`
- Artifact ID: `8821359178`
- Artifact digest: `sha256:1798561425aef1f3182e4a979498787bb31a0ef7a103be7eb4b34197aa4ef569`

## Final sealed result

- selected family: `disabled`
- selected threshold: `null`
- attenuated sealed decisions: `0`
- aggregate standard return: `0.0309676899827247`
- aggregate stress return: `0.027664740858863945`
- annualized standard return: `0.042901252067619966`
- maximum drawdown: `0.01054364964009935`
- status: `RETROSPECTIVE_NOT_YET_BREAKTHROUGH`

The five standard sealed-window returns remained exactly equal to v4.4:

1. `0.009467781764318728`
2. `0.01019534836961955`
3. `-0.0013321789942171147`
4. `0.010946400945364143`
5. `0.001377989360188625`

## Walk-forward family diagnosis

| Shock family | Eligible | Minimum fold excess | Positive folds | Compounded excess | Attenuated decisions | Ineligibility reasons |
|---|:---:|---:|---:|---:|---:|---|
| `drop_5` | No | `0.0` | 2/6 | `0.0005464702147346401` | 2 | fewer than four positive folds |
| `drop_20` | No | `-0.005832548931304693` | 1/6 | `-0.006899386754367409` | 18 | non-positive compounded excess; fewer than four positive folds; minimum deficit exceeded |
| `drawdown_20` | No | `-0.004317095791659398` | 3/6 | `0.004523122294154636` | 21 | fewer than four positive folds; minimum deficit exceeded |

### `drop_5` fold excess

- WF-1: `0.00025947595841269155`, 1 attenuation
- WF-2: `0.0`, 0 attenuations
- WF-3: `0.00030236419837015305`, 1 attenuation
- WF-4: `0.0`, 0 attenuations
- WF-5: `0.0`, 0 attenuations
- WF-6: `0.0`, 0 attenuations

This family was safe but too sparse.

### `drop_20` fold excess

- WF-1: `0.0`
- WF-2: `-0.005832548931304693`
- WF-3: `-0.002921828217756106`
- WF-4: `0.0`
- WF-5: `0.0016727648015623098`
- WF-6: `0.0`

This family was harmful and is rejected.

### `drawdown_20` fold excess

- WF-1: `0.00025947595841269155`, 1 attenuation
- WF-2: `-0.004317095791659398`, 12 attenuations
- WF-3: `0.0`, 0 attenuations
- WF-4: `0.0`, 0 attenuations
- WF-5: `0.005389738183325776`, 6 attenuations
- WF-6: `0.0031787598005619255`, 2 attenuations

This broadened the v4.8 pattern from two to three positive folds, but the prolonged WF-2 episode became more harmful and exceeded the frozen fold-loss allowance.

## What was learned

A rapid 5-day probability drop is too rare to provide broad validation evidence. Longer probability deterioration remains vulnerable to persistent low-state episodes.

The strongest diagnostic is duration:

- the harmful WF-2 episode attenuated 12 scheduled decisions;
- beneficial WF-5 and WF-6 episodes attenuated 6 and 2 decisions;
- the safe `drop_5` family acted only once in each of two folds.

This suggests the independent macro state may have value near the beginning of a deterioration episode, while repeatedly attenuating throughout a prolonged state can remove profitable crypto exposure.

## Next research boundary

Do not relax the eligibility rules, widen the sealed search, or revive `drop_20`.

A defensible subsequent hypothesis is a fresh-transition controller: use the original absolute dollar/rates probability state, but permit attenuation only for a predeclared short number of scheduled rebalances after a causal downward threshold crossing. This directly tests whether early episode information is useful while preventing persistent repeated attenuation.

The disabled v4.4 baseline must remain fallback. No live trading is authorized.