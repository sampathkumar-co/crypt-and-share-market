# v5.4.2 Forward Tail Integrity Implementation Contract

## Module

Implement `tradebot.research.forward_tail_integrity_v542` as a thin adapter over the frozen v4.2, v4.3, v4.4, v5.2, v5.3 and v5.4.1 modules.

The implementation must not modify any frozen strategy or simulator module. It may reuse their public helpers and must preserve their feature ordering and portfolio semantics.

## Tail-safe dataset

Create one row per asset for each eligible decision date. Feature construction must be semantically identical to `regime_ranking_v42.build_dataset`, but eligibility requires only 199 prior completed dates and the two opens required by return1.

For dates already emitted by the generic builder, exact equality is required for X, return1, dates, assets and feature names. Placeholder arrays for return3, return7, rank3, meta, downside3 and regimes must be deterministic and must never influence prediction or simulation.
## August source

Use Binance public daily spot kline archives for `BTCUSDT`, `ETHUSDT`, `SOLUSDT`, `XRPUSDT`, and `ADAUSDT` dated 2026-08-01. Parse with the existing audited daily-kline parser.

Reject missing archives, duplicate conflicts, invalid OHLC data, or a non-positive open. Persist source inventory keys, URLs, archive hashes and normalized open values.

## Evaluation

Merge the frozen through-June states with the exact v5.4.1 July extension. Do not merge August states. Supply August 1 opens separately to the tail dataset builder.

Predict with the frozen bundle, build a July 1-30 forward fold, apply the unchanged delayed candidate, and run the unchanged v5.3 window simulator at standard and stress costs.

The result report must identify v5.4.1 as a partial 23-day smoke, state that it is superseded only for date coverage, and preserve its report hash.
## Report and tests

The report must include protocol, contract and implementation hashes; prior v5.2, v5.3 and v5.4.1 report hashes; August source inventory hash; overlap counts; exact-equality booleans; decision-date count; activity dates; attenuated rebalance dates; both simulations; gates and final status.

Dedicated tests must cover:

- August URL construction and parser validation.
- Tail eligibility and July 30 return1 semantics.
- Exact overlap against the generic builder on synthetic complete data.
- Rejection of missing next opens and row-order mismatches.
- Gate and status transitions.
- Safety invariants: paper-only, no added asset, no increased target.

No live orders, exchange credentials, deposits, withdrawals, or shadow execution are authorized. A pass permits research continuation only; v4.4 remains the accepted baseline until all profitability gates pass.
