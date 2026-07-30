# Crypto Multi-Regime 4-Hour Discovery v1.4.2

## Status

This continuity correction is frozen before any v1.4 strategy return is calculated.

v1.4.1 fixed the asset universe, start date, split and Hyperliquid funding coverage before return access. Its exact Coinbase data-freeze attempt then failed closed because the public candle endpoint omitted a very small number of hourly intervals. No external-data fetch, strategy evaluation or holdout evaluation occurred in that failed run.

A separate metadata-only hourly-gap audit used the same Coinbase pagination code and calculated no price or strategy returns. The audit was workflow run `30515704605`, on head `f8bce6e488c435a23ad38181598cb8e4a1c814b7`. Its artifact digest is:

`sha256:5f87bc33b2efbcd2339ec76dbeaab233e4a2718b750f0df5594f12f21cc1e84c`

The audit found:

- 43 omitted hourly intervals across all eight assets combined;
- maximum missing count for one asset: 6 of 17,736 hours;
- maximum missing fraction: 0.00033829499323410016, or about 0.0338%;
- maximum consecutive gap: 6 hours;
- AVAX, DOT, APT, ARB and SUI omit 2025-10-25 16:00 through 20:00 UTC;
- OP omits 2025-10-25 15:00 through 20:00 UTC;
- NEAR and FIL omit 2024-10-26 16:00 UTC and 2025-10-25 16:00 through 20:00 UTC.

The common October 2025 timestamps across every product indicate an exchange-wide candle absence rather than independent asset-specific price gaps. The October 2024 NEAR/FIL omissions are isolated single hours.

## Unchanged research boundary

Everything frozen in v1.4.1 remains unchanged except the explicit continuity contract below:

- universe: AVAX, DOT, NEAR, FIL, APT, OP, ARB and SUI;
- Coinbase interval: 2023-11-15T00:00:00Z inclusive through 2025-11-23T00:00:00Z exclusive;
- 4,434 UTC-aligned four-hour bars;
- 600 warm-up bars;
- six discovery periods of 480 bars;
- 234-bar embargo;
- 720-bar final holdout from 2025-07-26 through 2025-11-22;
- all three alpha sleeves, thresholds, router priority, risk limits, fees, slippage, taxes and acceptance gates;
- completed-candle signals and next-bar-open execution;
- paper-only, long-or-cash, no leverage and no real-money authorisation.

The final holdout remains locked and has not been evaluated.

## Frozen bounded continuity contract

For each Coinbase product, first fetch all raw hourly candles from the frozen endpoint and preserve them separately. Construct the expected UTC hourly grid for the frozen interval and identify omitted timestamps.

Continuity is permitted only when every condition holds:

1. no more than 6 hourly timestamps are omitted for any asset;
2. the omitted fraction for every asset is no greater than 0.00035;
3. no consecutive omitted run exceeds 6 hours;
4. neither the first nor final expected hour is omitted;
5. at least one earlier raw or already continuity-filled candle exists;
6. the final completed hourly grid contains exactly 17,736 timestamps;
7. every synthetic timestamp is written to the immutable manifest.

For an eligible omitted hour, create a deterministic synthetic no-trade candle:

- timestamp: the omitted UTC hour;
- open: previous completed hourly close;
- high: previous completed hourly close;
- low: previous completed hourly close;
- close: previous completed hourly close;
- volume: 0.

Synthetic candles may only carry the immediately previous completed close forward. They cannot interpolate between future prices, use a later close, invent volume or alter any observed candle.

## Four-hour aggregation

After bounded continuity, aggregate each exact sequence of four UTC hourly candles into one four-hour candle:

- open from the first hour;
- high as the maximum of four highs;
- low as the minimum of four lows;
- close from the fourth hour;
- volume as the sum of four volumes.

The resulting dataset must contain exactly 4,434 complete four-hour candles per asset and exactly 4,434 common timestamps across all eight assets.

## Evidence requirements

The data manifest must preserve for every asset:

- raw hourly row count;
- completed hourly row count;
- synthetic row count;
- synthetic timestamps;
- longest synthetic run;
- raw hourly SHA-256;
- completed hourly SHA-256;
- four-hour SHA-256;
- first and last timestamps.

The freeze summary must state explicitly that no strategy or holdout return was calculated. It must record the Git head, protocol commit, dataset fingerprint and manifest hashes before discovery is allowed.

## Failure discipline

Any gap outside the bounded continuity contract fails closed. The rule cannot be expanded after discovery results are known. No alternative interpolation, exchange substitution, bar deletion or timestamp intersection may be introduced in v1.4.2.

Discovery remains a separate later workflow that may use only the immutable data-freeze artifact and committed hashes. A discovery rejection must not access the final 720-bar holdout.
