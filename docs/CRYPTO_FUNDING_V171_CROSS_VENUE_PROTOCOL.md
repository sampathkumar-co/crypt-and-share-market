# Crypto Funding Cross-Venue Replication v1.7.1 — Frozen Protocol

## Status and statistical role

This protocol is frozen before any v1.7.1 strategy return is calculated. It is paper-only, long-or-cash and unleveraged. It cannot authorise real-money trading.

The candidate is the exact unchanged v1.4.2 funding-exhaustion signal and exit. It was selected from known Coinbase-price diagnostics. v1.7.1 therefore does not claim fresh signal discovery. Its purpose is to test whether the same candidate replicates when the price venue is changed from Coinbase to Hyperliquid while the funding, macro, stablecoin, cost and execution rules remain unchanged.

The Hyperliquid price replication periods are an independent price-source test but not an independent time test. The final 720-bar interval remains the only independent time test.

No signal threshold, funding calculation, entry rule, exit rule, cost assumption or existing external-factor value may be changed.

## Pre-return coverage correction

The metadata-only audit run `30521109767` showed that the complete eight-asset common Hyperliquid four-hour interval begins at `2024-04-18T00:00:00Z` and ends at `2025-11-22T20:00:00Z`.

- Coverage artifact ID: `8750771782`.
- Coverage artifact digest: `sha256:8aaacd5de9ab955cb326aa202587f0e2832cddfd54711ab1db00c828b55ad905`.
- Common rows per asset: 3,504.
- The unavailable history is one continuous 930-bar prefix, not random internal gaps.
- DOT's single earlier candle at `2024-04-17T20:00:00Z` is excluded to preserve an exact common universe.

No strategy return was calculated before this correction.

## Price data contract

Fetch read-only Hyperliquid public `candleSnapshot` data for exactly:

- coins: APT, ARB, AVAX, DOT, FIL, NEAR, OP and SUI;
- interval: `4h`;
- start: `2024-04-18T00:00:00Z`;
- end exclusive: `2025-11-23T00:00:00Z`.

For each asset:

- require exactly 3,504 unique timestamps;
- require the first timestamp to be `2024-04-18T00:00:00Z`;
- require the last timestamp to be `2025-11-22T20:00:00Z`;
- reject duplicates, missing timestamps, unexpected timestamps, malformed OHLCV values, non-positive prices or negative volume;
- preserve the raw JSON response, normalized CSV and SHA-256 hashes;
- do not synthesize or fill any price candle.

The normalized CSV contract is `timestamp,open,high,low,close,volume`, with timestamps in UTC and symbol names matching the existing evaluator: APTUSDT, ARBUSDT, AVAXUSDT, DOTUSDT, FILUSDT, NEARUSDT, OPUSDT and SUIUSDT.

The complete price artifact and canonical reloaded-CSV fingerprint must be frozen before any return is calculated.

## External-factor contract

Reuse the immutable v1.4.2 external-factor directory byte-for-byte:

- source workflow run: `30516539776`;
- source artifact ID: `8749247737`;
- external manifest SHA-256: `cae89da7cc39353a51eba72cd14f1c66eeff88d059c32283d4248694e3bfa5ae`.

Funding remains Hyperliquid settled-funding history under the existing calendar-normalised model. Coin Metrics stablecoin and FRED macro files remain unchanged.

## Frozen temporal split

The 3,504 common bars are divided exactly as follows:

- warm-up: 600 bars, `2024-04-18T00:00:00Z` through `2024-07-26T20:00:00Z`;
- cross-venue replication: five contiguous, non-overlapping periods of 390 bars each;
- replication interval: `2024-07-27T00:00:00Z` through `2025-06-16T20:00:00Z`;
- embargo: 234 bars, `2025-06-17T00:00:00Z` through `2025-07-25T20:00:00Z`;
- independent final holdout: 720 bars, `2025-07-26T00:00:00Z` through `2025-11-22T20:00:00Z`.

The replication workflow must not invoke holdout mode.

## Frozen candidate

Use exactly the existing v1.4.2 `funding_only` sleeve under the calendar-normalised settled-funding model:

- seven-calendar-day funding mean using exactly 42 four-hour buckets;
- exactly 720 prior seven-day means for the 120-day reference distribution;
- absent settlement buckets treated as zero settled funding cashflow;
- negative current funding at or below its prior tenth percentile;
- price drawdown of at least 15% from the preceding 120-bar completed-close high;
- recent-low condition and 12-bar EMA recovery cross;
- unchanged funding recovery, 72-bar EMA, 2.25 ATR stop and 60-bar maximum-hold exits;
- completed-candle signals and next-bar-open fills.

No new signal, threshold or parameter search is permitted.

## Fixed sizing profiles

Evaluate exactly the same predeclared profiles as v1.6.1:

### Primary balanced

- maximum positions: 2;
- maximum asset weight: 25%;
- minimum cash reserve: 50%;
- volatility target: 25% annualised;
- maximum permitted period drawdown: 8%.

### Original exposure diagnostic

- maximum positions: 3;
- maximum asset weight: 25%;
- minimum cash reserve: 25%;
- volatility target: 30% annualised.

### Defensive exposure diagnostic

- maximum positions: 2;
- maximum asset weight: 12.5%;
- minimum cash reserve: 75%;
- volatility target: 20% annualised.

Existing drawdown brakes, fees, slippage and tax models remain unchanged.

## Cost stresses

For each period and profile, record:

- normal after-cost return;
- standard stress: normal return minus 15 basis points per unit of turnover;
- double-cost stress: normal return minus 30 basis points per unit of turnover.

## Cross-venue replication gates

Replication passes only if every condition holds:

- exactly five replication periods are present and all five are active;
- at least three of five primary periods are profitable;
- primary average, median and compounded returns are positive;
- the average of the first two periods and the average of the final three periods are positive;
- primary standard-stressed and double-cost-stressed average returns are positive;
- primary average return exceeds cash and Hyperliquid equal-weight buy-and-hold;
- primary worst period drawdown is at most 8%;
- at least six distinct assets are selected;
- maximum traded-notional concentration in one asset is at most 35%;
- all five leave-one-period-out average returns are positive;
- at least six of eight leave-one-asset-out primary reruns have positive average returns;
- original-exposure and defensive-exposure profiles each have positive normal, standard-stressed and double-cost-stressed average returns.

Failure of any gate is preserved as valid negative evidence and keeps all holdouts locked.

## Dual-venue one-shot holdout rule

If and only if cross-venue replication passes, a separate workflow may evaluate the exact candidate once on the same independent 720-bar time interval using:

1. the frozen Coinbase v1.4.2 price artifact; and
2. the frozen Hyperliquid v1.7.1 price artifact.

Only the primary balanced sizing profile may be used. No redesign, alternate profile, retry or replacement candidate is allowed.

Each venue must independently satisfy:

- at least two of three holdout periods active;
- at least two of three holdout periods profitable;
- positive average and compounded returns;
- positive standard-stressed and double-cost-stressed average returns;
- average return above cash and that venue's equal-weight buy-and-hold;
- worst period drawdown at most 8%;
- at least three distinct assets selected;
- maximum traded-notional concentration at most 45%.

Additionally, the mean of the two venue-level average returns must be positive. A successful dual-venue holdout authorises only a later shadow-paper stage, never live trading or a profit claim.
