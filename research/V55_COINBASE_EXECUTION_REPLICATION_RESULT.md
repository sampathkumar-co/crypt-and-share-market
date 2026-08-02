# v5.5 Coinbase Execution-Source Replication Result

## Decision

Status: `COINBASE_EXECUTION_REPLICATION_PASSED`.

The frozen Binance signal and decision manifest reproduced exactly, while all July execution returns were independently replaced with Coinbase Exchange daily open-to-open returns.

- Report SHA-256: `3cb5ceff2a390b40107177e86bb7236076388896cc0cff847117c917cdee9279`.
- Decision-manifest SHA-256: `6a8a6fad962abd8a117f3f88ab003bbcb58f4d7e20e6c9b5856fd9944e9f7701`.
- Coinbase source inventory SHA-256: `28047f2a6520d928b6e074048a08b7693f33bfb8307b681ab7f76672d46b90d2`.
- Products: BTC-USD, ETH-USD, SOL-USD, XRP-USD and ADA-USD.
- Required opens: 31 per asset, July 2 through August 1, 2026.
## Replay integrity

- 150 July date-asset return rows were replaced.
- Features, dates, assets, target arrays and all non-return fields remained exact.
- Returns outside the replay period remained exact.
- Binance/Coinbase one-day return correlation was 0.999620.
- The single attenuated selected rebalance remained July 4.
- Baseline and candidate each executed eight target-changing actions.
- Drawdown did not worsen and no asset or target was added.

## Performance

- Standard Coinbase baseline return: 0.577931%.
- Standard Coinbase candidate return: 0.577941%.
- Standard excess: +0.00000986 percentage points.
- Stress Coinbase baseline return: 0.537431%.
- Stress Coinbase candidate return: 0.539957%.
- Stress excess: +0.002526 percentage points.

All predeclared independent execution-source gates passed. The standard edge is extremely small, so this is replication and safety evidence—not a profitability breakthrough. v4.4 remains accepted, and no live trading or automatic shadow execution is authorized.
