# v2.7 Regime-Conditioned Pullback Discovery Protocol

## Status

This protocol is frozen before implementation and before accessing any v2.7 outcome. It is historical research only. It cannot modify, shorten, replace, or reinterpret Track A, v2.5, v2.6, any frozen gate, or any forward evidence.

## Motivation

v2.6 established that confirmed residual continuation remained sparse and negative before and after costs. The next experiment must therefore test a genuinely different mechanism: buying controlled pullbacks inside an established positive regime after short-term selling pressure is absorbed, rather than chasing continuation after an impulse.

## Safety boundary

- Paper-only and long-or-cash.
- No leverage, shorts, options, wallets, credentials, or order placement.
- Maximum one open position.
- Fixed target weight: 15% of portfolio; at least 85% cash.
- Signal uses completed candles only.
- Earliest fill is the next completed 5-minute bar open after confirmation.
- Standard round-trip cost: 20 bps. Stress cost: 40 bps.
- No writes to `forward-data/v2` or any Track A result path.

## Assets and source data

Assets: BTC, ETH, SOL, AVAX, LINK, DOGE against USDT.

Use public Binance spot and USD-M futures archives only. Build completed 5-minute bars and derive hourly regime features without look-ahead. Record SHA-256 for every archive.

## Frozen windows

Each window contains a 10-day warm-up followed by a 21-day scoring period. Windows are non-overlapping.

Discovery windows:

1. 2024-08-01 through 2024-08-21
2. 2024-11-01 through 2024-11-21
3. 2025-02-01 through 2025-02-21

Validation windows, never used to alter thresholds:

1. 2025-05-01 through 2025-05-21
2. 2025-08-01 through 2025-08-21
3. 2025-11-01 through 2025-11-21
4. 2026-02-01 through 2026-02-21
5. 2026-05-01 through 2026-05-21

A breakthrough requires all five validation windows to produce positive net compounded return after standard costs. Aggregate positivity alone is insufficient.

## Frozen market regime

At signal time, an asset is eligible only when all conditions hold:

- 24-hour spot return is positive.
- Spot close is above the 72-hour volume-weighted mean close.
- 24-hour cross-sectional residual return versus BTC is non-negative.
- 24-hour trend efficiency is at least 0.30.
- 24-hour realized volatility is below 2.5 times its trailing 7-day median.
- BTC itself is above its 72-hour volume-weighted mean close.

## Frozen pullback mechanism

The signal is a controlled retracement, not momentum continuation. During the last completed hour:

- spot return is between -1.50% and -0.15%;
- the close remains above the 24-hour volume-weighted mean close;
- close location within the hourly range is at least 0.35;
- spot taker imbalance is negative;
- perpetual taker imbalance is no more positive than spot imbalance plus 0.15;
- open interest change is between -3.0% and +1.0%;
- basis is not more than 20 bps above its trailing 72-hour median;
- funding is below 0.05% per eight hours.

## Frozen absorption confirmation

Use the next three completed 5-minute bars. Confirm only when:

- cumulative spot return is non-negative;
- at least two of three bars close above their midpoints;
- cumulative taker imbalance improves relative to the signal hour;
- price does not trade more than 0.60% below the signal-hour low;
- the final confirmation close is above the first confirmation open.

Enter at the next 5-minute open. If confirmation fails, discard the signal.

## Ranking and overlap

When multiple assets confirm simultaneously, choose exactly one using this fixed score:

`2.0 * regime_efficiency + 1.5 * residual_24h + 1.0 * absorption_return - 1.0 * abs(signal_hour_return) - 0.5 * volatility_ratio`

Normalize each term cross-sectionally using only information available at confirmation time. Ties are resolved lexicographically by asset symbol.

No new signal may be accepted while a position is active. After exit, impose a four-hour cooldown.

## Frozen exits

Evaluate fixed holding horizons of 1, 2, 4, and 8 hours from entry. The primary horizon is 4 hours. No stop-loss, take-profit, trailing exit, or intra-window tuning is permitted in v2.7.

## Required report

For each horizon and cost level, report:

- gross and net compounded return;
- event count and win rate;
- maximum drawdown;
- discovery-window returns;
- each of the five validation-window returns;
- asset and month concentration;
- benchmark returns at equal 15% exposure;
- event-level signal, confirmation, entry, and exit timestamps;
- deterministic report hash and source inventory.

## Acceptance gates

The primary 4-hour standard-cost result is a candidate breakthrough only if every gate passes:

- at least 40 total events;
- at least 20 validation events;
- at least four active assets;
- no asset contributes more than 50% of positive P&L;
- positive aggregate discovery return;
- positive aggregate validation return;
- positive net return in each of all five validation windows;
- positive 4-hour stress-cost return;
- positive 2-hour and 8-hour standard-cost sensitivity returns;
- maximum drawdown no greater than 8%;
- beats cash, 15%-BTC, and 15%-equal-weight benchmarks;
- no missing source window and no excluded event caused by absent exit prices.

Even if all gates pass, the result is historical evidence only. It does not authorize trading or alter Track A.