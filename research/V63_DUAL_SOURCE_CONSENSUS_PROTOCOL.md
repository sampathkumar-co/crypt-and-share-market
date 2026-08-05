# v6.3 Dual-Source Consensus Ensemble

Status: frozen after v6.2 rejection and before any v6.3 market outcome is calculated.

## Preserved evidence

- v6.0.2 and v6.0.3 statistical failures remain authoritative.
- v6.1 remains the strongest prior candidate and remains rejected because two Coinbase bootstrap bounds and the median rank threshold failed.
- v6.2 remains rejected; its authoritative report SHA is `e56cdaa5b859da435a32f68f12691a15dd45a23792671e01831d122ea96c97a5`.

No prior gate or result is rewritten.

## Objective

Test one symmetric, parameter-free response to source-specific instability: allow crypto exposure only to the extent that the fixed Binance and Coinbase v6.1 ensembles independently support the same asset exposure.

## Frozen signal engines

Run the exact v6.1 16-member equal-weight neighborhood separately on:

- completed Binance BTC/ETH daily history;
- completed Coinbase BTC/ETH daily history.

Each source engine preserves corrected scheduled execution, member natural drift, the 10-day cadence, the same 10% member target cap and no hidden daily normalization.

## Dual-source target

On a day when either source engine has a genuine member decision or risk-off exit, calculate each source's pre-return equal-weight ensemble target. For each asset:

`dual_weight = min(binance_source_weight, coinbase_source_weight)`

Between genuine source decisions, the real dual-source portfolio carries its natural drift unchanged.

This rule has no fitted coefficient, threshold or favored source. It can never initiate more exposure than either v6.1 source engine. Disagreement automatically moves capital toward yielding cash.

## Execution-source replication

The exact same dual-source target sequence is evaluated twice:

1. Binance opens as the execution source;
2. Coinbase opens as the execution source.

Signals always use both sources. Execution-source replication therefore tests fill-price robustness, not independent signal replication. This distinction must be explicit.

## Frozen evaluations

- verification years 2021-2025;
- standard 20-bps and stress 40-bps round-trip costs;
- one-additional-day signal delay;
- natural drift and terminal liquidation;
- material profitability, drawdown, action and concentration gates;
- source-specific 20/60/120-day moving-block bootstrap with 10,000 deterministic resamples;
- DSR using 227 direct-lineage trials and frozen corrected-grid Sharpe dispersion `0.17603369374678823`;
- unchanged 35-split rank-stability thresholds against the 16 fixed Binance v6.1 members: top-half fraction >= 0.80 and median percentile >= 0.60.

## Material gates

No relaxation from v6.1:

- annualized standard return >= 5%;
- annualized excess over yielding cash >= 2 percentage points;
- positive stress return;
- four of five standard years positive;
- three of five stress years positive;
- at least 30 target-changing actions;
- maximum drawdown <= 5%;
- conservative delayed excess over cash > 0;
- maximum positive decision-interval share <= 20%;
- maximum positive year share <= 50%.

## Outcomes

- `DUAL_SOURCE_CONSENSUS_REJECTED`
- `RETROSPECTIVE_DUAL_SOURCE_CANDIDATE_FORWARD_REQUIRED`

All dates are exposed. A pass is only a retrospective candidate and must enter a new sealed forward programme. It does not alter v3.3 and cannot authorize live or continuous paper trading.
