# v4.8 Cross-Exchange Confirmation — Result Diagnosis

## Verdict

v4.8 completed successfully, including complete Coinbase BTC-USD and ETH-USD source validation, causality checks, six independently refitted controls, eighteen independently refitted augmented models, standard/stress evaluation and evidence archival.

No active family passed the frozen robustness rules, so the exact v4.4 baseline was retained.

## Reproduced control

- aggregate standard return: `0.0309676899827247`
- aggregate stress return: `0.027664740858863945`
- annualized standard return: `0.042901252067619966`
- maximum drawdown: `0.01054364964009935`
- report SHA-256: `1fe07d3ad6b12fb1b56818615db6f6bc9bdb8a2331d7f02322a7a219e0d83675`

## Family results

### Price confirmation

- positive standard-excess folds: 3 of 6
- compounded standard excess: `-0.04365176839117557`
- compounded stress excess: `-0.04469589912142502`
- worst standard fold excess: `-0.027635052896195278`

Price-only confirmation was decisively rejected.

### Liquidity confirmation

- positive standard-excess folds: 3 of 6
- compounded standard excess: `-0.014055547402307522`
- compounded stress excess: `-0.013909530136706039`
- worst standard fold excess: `-0.013439787765037403`

Liquidity-only confirmation was also rejected.

### Combined confirmation

- positive standard-excess folds: 4 of 6
- compounded standard excess: `0.009984413704651862`
- compounded stress excess: `0.009683125416978022`
- worst standard fold excess: `-0.009268394850919792`

Combined confirmation was the first new feature family to produce positive aggregate excess under both standard and stress costs while improving four of six validation quarters. It failed only the worst-fold and drawdown-stability rules.

## Fold diagnosis for combined confirmation

| Fold | Standard excess | Control actions | Combined actions | Control turnover | Combined turnover |
|---|---:|---:|---:|---:|---:|
| WF-1 | `+0.0037315132` | 2 | 14 | `0.1005999645` | `0.7122629672` |
| WF-2 | `-0.0041064439` | 23 | 31 | `1.2037784698` | `1.7199325072` |
| WF-3 | `-0.0092683949` | 24 | 13 | `1.2553264010` | `0.6303591874` |
| WF-4 | `+0.0055269627` | 17 | 17 | `0.9073799989` | `0.8143538305` |
| WF-5 | `+0.0137795902` | 10 | 12 | `0.5879502599` | `0.6069381218` |
| WF-6 | `+0.0004396792` | 4 | 2 | `0.1939533656` | `0.0944858087` |

The two losing folds failed in opposite ways:

- WF-2: combined confirmation overtraded and its stress drawdown exceeded the control by approximately `0.0051233`.
- WF-3: combined confirmation undertraded a strong control period, using 13 actions versus 24 and approximately half the turnover.

This is not evidence that the combined source lacks information. It is evidence that fully replacing the stable control model with the augmented model is too unstable.

## Frozen next step

v4.9 should retain independently trained control and combined-confirmation models and blend their target portfolios with fixed, pre-registered weights. The blend must preserve the control as the majority allocation unless walk-forward evidence supports otherwise.

A target ensemble can retain some of the approximately 1% aggregate cross-exchange excess while mechanically bounding the damage from either overtrading or undertrading by the augmented model.

The robustness gate must not be relaxed. Paper-only. No live-trading authorization.
