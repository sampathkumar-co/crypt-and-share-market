# Crypto Funding v1.7 — Cross-Venue Coverage Audit

## Purpose

Before defining or evaluating a cross-venue replication protocol, audit whether Hyperliquid public four-hour candles cover the complete frozen v1.4.2 interval for APT, ARB, AVAX, DOT, FIL, NEAR, OP and SUI.

This stage is metadata-only. It must not calculate strategy returns, compare profitability, access any price after the frozen end timestamp or evaluate the final holdout.

## Requested interval

- Start: `2023-11-15T00:00:00Z`
- End: `2025-11-22T23:59:59.999Z`
- Expected aligned four-hour timestamps: 4,434 per asset.

## Audit output

For every asset, record:

- response row count;
- unique completed four-hour candle count;
- first and last returned timestamps;
- duplicate timestamps;
- missing timestamps against the exact requested grid;
- longest consecutive missing run;
- whether the complete 4,434-timestamp contract is satisfied.

The audit must preserve the raw response-derived timestamp list and an atomic JSON report. It must not fill gaps or infer prices.

## Decision

A replication protocol may be frozen only if all eight assets have exact complete coverage, or if a separately documented pre-return universe/interval correction leaves a statistically useful common interval. Any correction must be committed before strategy returns are calculated.
