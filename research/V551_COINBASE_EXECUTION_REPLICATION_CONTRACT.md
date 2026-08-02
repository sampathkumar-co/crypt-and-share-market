# v5.5.1 Coinbase Execution Replication Contract

## Module and source reuse

Implement `tradebot.research.coinbase_execution_replication_v55`.

Reuse the audited Coinbase downloader, chunking, URL construction and candle parser from `historical_coinbase_replication_v32`. Extend only the frozen product mapping and requested date range. Do not create a second parser.

Reuse v5.4.2 for Binance source reconstruction, tail-safe features, predictions, candidate activity and safety checks. Frozen strategy modules must not be edited.

## Decision manifest

Provide a manifest-only command that performs no Coinbase candle download. It must write canonical JSON containing provenance hashes and one row per July decision date.

Each row must contain the date, regime, selected assets, panic flag, due flag, baseline target assets, raw activity, delayed activity, candidate multiplier, selected-rebalance flag and attenuated-rebalance flag. Include a canonical manifest SHA-256.
## Coinbase replay

Download daily candles for BTC-USD, ETH-USD, SOL-USD, XRP-USD and ADA-USD covering the required opens. Record request URLs, response SHA-256 values, parsed row counts and normalized open hashes.

Reject missing dates, conflicting duplicates, malformed candles, non-positive opens or a product mismatch. No interpolation, forward fill, stablecoin conversion or cross-product substitution is allowed.

Create a copy of the v5.4.2 Dataset with X and every non-return field unchanged. Replace return1 for July 1-30 rows only. Assert all non-return arrays, row order and feature names are exact before replay.

Generate the same probabilities from the committed delayed activity and run the unchanged standard/stress simulations. Compare candidate to a Coinbase-executed baseline using identical decisions and costs.
## Report and tests

The report must include protocol, contract, implementation, v5.4.2 report and decision-manifest hashes; Coinbase source inventory and normalized-open hashes; source completeness; manifest reproduction; replaced-row count; Binance-versus-Coinbase return correlation; both simulations; gates and status.

Dedicated tests must cover product URLs, chunk boundaries, parser reuse, missing-open rejection, exact decision-manifest reproduction, return replacement limited to July rows, unchanged features, positive/negative gate outcomes and safety invariants.

The implementation must remain paper-only. It must not access credentials, private Coinbase endpoints, order APIs, deposits, withdrawals or live exchange state. No result may trigger an order or automatic strategy promotion.
