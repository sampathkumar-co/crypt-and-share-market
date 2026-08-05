# v6.3.5 Common-Source 200-Day Warmup Addendum

Status: frozen after the v6.3.4 run failed closed and before any v6.3 market outcome, rank result, bootstrap result, or candidate status was calculated.

## Observed infrastructure defect

The common-source discovery rank audit requires the exact frozen v3.1 feature set, including 200-day returns and moving averages, with a one-day signal lag. The existing Coinbase v3.2 replication dataset begins on 2020-06-14 because it was designed only for the 2021-2025 verification period. It therefore cannot produce a causal feature row for 2020-07-01, even though raw Coinbase BTC-USD and ETH-USD daily candles exist earlier.

Changing the discovery start by one or several days cannot repair a 200-day feature warmup deficit. Repeatedly shortening the audit interval would also weaken the preregistered 35-split rank-stability test and is forbidden.

## Frozen correction

Before evaluating v6.3, download genuine Coinbase Exchange public daily candles for BTC-USD and ETH-USD from exactly 200 calendar days before 2020-07-01 through 2020-06-13 inclusive. The existing frozen Coinbase history from 2020-06-14 onward remains unchanged.

- Warmup start: 2019-12-14 UTC.
- Warmup end: 2020-06-13 UTC.
- Provider: Coinbase Exchange public REST candles endpoint.
- Granularity: 86,400 seconds.
- Maximum request chunk: the existing frozen 250-day limit.
- Every raw response and normalized candle set must be SHA-256 recorded.
- Complete daily coverage is mandatory for both assets.
- Conflicting duplicate candles fail closed.
- Warmup candles may construct lagged features only; they are not evaluation returns and cannot contribute P&L.

## Unchanged evaluation

The v6.3.4 common discovery periods remain 2020-07-02 through 2020-09-30 and 2020-10-01 through 2020-12-31. The one-day signal lag, 183 aligned evaluation observations, 16 frozen comparison members, 35 rank splits, standard and stress costs, delayed execution, material gates, moving-block bootstrap, Deflated Sharpe, rank thresholds, verification years, and all safety boundaries remain unchanged.

No fitted parameter, source preference, threshold, member weight, holdout, acceptance criterion, or strategy mechanism is changed. This addendum does not authorize continuous paper or live trading.