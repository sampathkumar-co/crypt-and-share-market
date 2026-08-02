# v5.2 Adversarial Alpha Funnel Result

## Outcome

The frozen v5.2 campaign completed successfully without sealed evaluation.
It generated exactly 100,000 hypotheses and reduced them through behavioral
deduplication, proxy screening, adversarial attacks and exact simulation.

- Raw hypotheses: 100,000
- Distinct intervention behaviors: 68,199
- Proxy-eligible behaviors: 1,838
- Full proxy-attack survivors: 193 of the top 512
- Exact standard-and-stress survivors: 64 of 64 tested
- Deep exact-neighbor survivors: 14 of 16 tested
- Frozen shortlist: 3 distinct mechanism families

Report SHA-256:
`60f4d1b88dc0ef66d64a8ec4e192a56fdaf76a07182bde8e1567f17a61313ab2`

## Primary mechanism: trend-acceleration recovery

Frozen rule:

- Source: cross-asset mean seven-day spot return
- Transform: ten-day acceleration
- Normalization: rolling 90-day percentile using prior observations only
- Event: upward crossing of percentile 0.30
- Persistence: seven days
- Exposure multiplier: 0.75

Exact exposed walk-forward result:

- Six of six folds positive
- Compounded standard excess: 0.6033346886%
- Minimum standard fold excess: 0.0129737979%
- Six of six doubled-cost folds positive
- Compounded doubled-cost excess: 0.6457557310%
- Twenty attenuated decisions

The primary rule remained positive under exact one-day delay, both adjacent
thresholds and both adjacent history windows. Removing its best fold also left
positive compounded proxy excess, and it exceeded the circular-shift placebo
set.

## Secondary mechanism: breadth reversal

Frozen rule:

- Source: fraction of assets above their 50-day moving average
- Transform: ten-day change
- Normalization: rolling 20-day percentile using prior observations only
- Event: downward crossing of percentile 0.30
- Persistence: seven days
- Exposure multiplier: 0.75

Exact exposed walk-forward result:

- Six of six folds positive under standard and doubled costs
- Compounded standard excess: 0.4849754095%
- Compounded doubled-cost excess: 0.5122893592%
- Thirteen attenuated decisions

## Interpretation

This is the strongest mechanism-level result so far. It is broader and more
stable than the absolute macro-threshold results because two economically
different relative-state mechanisms improved every exposed fold.

It is not yet a final profitability breakthrough. All six discovery folds are
now exposed, and v5.2 deliberately performed no final evaluation.

The source archive extends through June 30, 2026. Dates from October 1, 2025
through June 30, 2026 were not used by generation, deduplication, screening,
attacks or exact ranking. v5.3 must freeze the primary rule before evaluating
that untouched nine-month interval once.

The accepted strategy remains v4.4 until that replication and subsequent paper
observation succeed. No live trading is authorized.
