# v5.5 Coinbase Execution-Source Replication Protocol

## Purpose

v5.4.2 passed a complete July 1-30 forward smoke using Binance spot execution prices. v5.5 tests whether the same frozen signals and target decisions remain beneficial when execution returns come from an independent venue.

This is an execution-source replication only. Coinbase data must not affect features, model predictions, candidate activity, asset selection, risk state, thresholding, or portfolio target generation.

## Frozen evidence dependency

- v5.4.2 report SHA-256: `0219a929a5abf55dbfed719ecad7dbd90bdbda84cab3a2a3d9fb8f72206859d2`.
- Decision period: 2026-07-01 through 2026-07-30.
- Frozen bundle training end: 2025-06-30.
- Frozen candidate and one-decision-day delay are unchanged.
- Accepted baseline remains v4.4 yield-bearing cash.
## Independent execution universe

Coinbase Exchange public REST products are frozen as:

- BTC -> BTC-USD
- ETH -> ETH-USD
- SOL -> SOL-USD
- XRP -> XRP-USD
- ADA -> ADA-USD

All five product metadata endpoints returned HTTP 200 before this protocol was frozen. Daily candles use granularity 86,400 seconds.

Required execution opens are 2026-07-02 through 2026-08-01 inclusive. For decision date D, the execution return is Coinbase open(D+2) / Coinbase open(D+1) - 1, matching the frozen next-open semantics.

Coinbase volume, close, high, low, listing metadata, or any other field must not enter signal generation. Candle OHLC fields may be validated, but only open values may replace execution returns.
## Decision-manifest freeze

Before downloading Coinbase candles, v5.5 must produce and commit a canonical Binance decision manifest for all 30 dates. The manifest must include date, predicted regime, selected assets, due/panic state, delayed candidate activity, and whether an attenuated selected rebalance occurs.

The manifest is generated only from the frozen through-July Binance source, v4.3 bundle and v5.4.2 candidate. Its SHA-256 becomes a hard dependency of the Coinbase replay. Coinbase results may never change or regenerate this manifest.

## Replay method

Construct the exact v5.4.2 tail dataset and predictions. Verify its report hash, source hashes, 30 dates, overlap integrity, activity dates and attenuated rebalance dates.

Copy the dataset and replace return1 only for the 30 July decision rows with Coinbase open-to-open returns. Leave all features, dates, assets, predictions and target decisions unchanged. Run the unchanged v5.3 standard/stress simulator for both baseline and candidate.
## Replication gates

A pass requires every gate below:

- All five Coinbase products and every required open are present and valid.
- The committed Binance decision manifest is reproduced exactly.
- Thirty July decision dates and the single July 4 attenuated rebalance are unchanged.
- Standard Coinbase execution excess is positive.
- Stress Coinbase execution excess is positive.
- At least one attenuation is executed.
- Standard and stress maximum drawdown are no more than 25 basis points worse than their Coinbase baselines.
- Target-changing actions do not increase.
- No asset is added and no target exceeds the baseline.

Possible statuses are `COINBASE_EXECUTION_DATA_INCONCLUSIVE`, `COINBASE_EXECUTION_NO_SIGNAL`, `COINBASE_EXECUTION_REPLICATION_PASSED`, and `COINBASE_EXECUTION_REPLICATION_FAILED`.

A pass supplies independent execution-source evidence only. It does not satisfy the five-percent annualized profitability gate, authorize live trading, or replace v4.4.
