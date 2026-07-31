# v2.7 Pullback and Recovery Historical Discovery Protocol

## Status

Frozen before implementation and before any v2.7 outcome access.

This protocol is historical research only. It cannot alter, replace, shorten, or unlock the live-forward Track A campaign, its evidence, gates, holdout, or trading authorization.

## Failure being addressed

The v2.5 and v2.6 continuation screens entered after persistent positive momentum. The surviving v2.6 events still bought after two positive completed hours and then frequently reversed. v2.7 therefore tests mechanisms that avoid chasing the impulse:

1. residual impulse followed by a controlled pullback and reclaim;
2. derivatives capitulation followed by stabilization and recovery;
3. volatility compression followed by breakout and successful retest.

No v2.5 or v2.6 threshold is modified.

## Mode and safety

- Mode: `HISTORICAL_PULLBACK_RECOVERY_DISCOVERY_ONLY`.
- Paper-only, long-or-cash.
- No leverage, shorting, wallets, credentials, order placement, or execution authorization.
- Maximum one position at a time.
- Position weight: 15%.
- Minimum cash: 85%.
- Every report must set `authorizes_trading=false`, `authorizes_shadow_paper=false`, and `changes_track_a=false`.
- Results persist only to `historical-results/v27`; never to `forward-data/v2`.

## Assets and source data

Assets: BTC, ETH, SOL, AVAX, LINK, and DOGE against USDT.

Only public Binance archives may be used:

- completed 5-minute spot klines;
- completed 5-minute USD-M perpetual klines;
- funding-rate archives;
- open-interest metrics archives.

Every downloaded archive URL and SHA-256 hash must be recorded. Missing required data fails the affected window closed. No interpolation or fabricated bars are permitted.

## Fixed windows

Each screen uses a ten-day warm-up. Outcomes from the five verification windows must not be inspected until implementation, tests, thresholds, costs, and gates are committed.

### Discovery windows

1. `2024-03`: March 1-30, 2024.
2. `2024-06`: June 1-30, 2024.
3. `2024-09`: September 1-30, 2024.

### Independent verification windows

1. `2024-12`: December 1-30, 2024.
2. `2025-03`: March 1-30, 2025.
3. `2025-06`: June 1-30, 2025.
4. `2025-09`: September 1-30, 2025.
5. `2025-12`: December 1-30, 2025.

The five verification windows are scored separately. Aggregating them cannot rescue a negative verification window.

## Completed-data chronology

For every candidate:

- `t-2` is the completed setup/impulse/flush hour;
- `t-1` is the completed pullback/stabilization/breakout hour;
- `t` is the completed reclaim/recovery/retest hour;
- earliest entry is the open of `t+1`;
- primary exit is the open six hours after entry.

Sensitivities are three-hour and twelve-hour exits. No value from entry or later may participate in candidate construction.

## Shared calculations

- BTC beta uses exactly 168 completed hourly returns ending before the evaluated setup hour.
- Residual returns subtract beta-adjusted BTC returns.
- Percentile references use only the preceding 168 completed hours and exclude the current setup sequence.
- Hourly path features are reconstructed from exactly twelve ordered completed 5-minute bars.
- Round-trip cost is subtracted once from raw asset return before multiplying by portfolio weight.

## Family 1: residual pullback reclaim

At `t-2`:

- 12-hour residual return >= 1.5%;
- 48-hour residual return >= 2.5%;
- both are at or above their trailing 80th percentiles;
- raw 12-hour return is positive and <= 10%;
- spot trend efficiency >= 0.35;
- spot close location >= 0.55;
- absolute basis <= 25 bps;
- funding <= 0.015%.

At `t-1`:

- return is between -2.5% and -0.2%;
- pullback retraces between 20% and 65% of the raw 12-hour impulse;
- close remains above the midpoint of the 12-hour impulse;
- quote volume is no more than 125% of `t-2` volume;
- open interest does not fall more than 4% from `t-2`.

At `t`:

- return is between +0.2% and +3.0%;
- close exceeds the midpoint of the `t-1` range;
- taker imbalance >= 0.05;
- spot-minus-perpetual flow lead >= 0.03;
- trend efficiency >= 0.35;
- close location >= 0.55;
- quote volume exceeds `t-1` volume;
- absolute basis <= 25 bps.

## Family 2: capitulation stabilization recovery

At `t-2`:

- six-hour raw return <= -3.0%;
- six-hour residual return <= -2.0%;
- both are at or below their trailing 15th percentiles;
- six-hour open-interest change <= -2.5%;
- basis is negative or funding is at/below its trailing 15th percentile;
- close location <= 0.40 or range is at/above its trailing 80th percentile.

At `t-1`:

- low is no more than 1.5% below the `t-2` low;
- close location >= 0.35;
- taker imbalance improves by at least 0.08 from `t-2`;
- open interest declines no more than a further 1.5%.

At `t`:

- return >= +0.25%;
- close recovers at least 45% of the `t-2` intrahour range;
- taker imbalance >= 0.05;
- flow lead >= 0.03;
- basis improves by at least 1 bp from `t-2`;
- open interest declines no more than a further 1.0%.

## Family 3: compression breakout retest

During the twelve hours ending at `t-2`:

- mean realized volatility is at/below the trailing 35th percentile;
- mean hourly range fraction is at/below the trailing 35th percentile.

At `t-1` breakout:

- return is between +0.5% and +3.5%;
- residual one-hour return is positive;
- quote volume is at least 1.4 times the prior 24-hour median;
- trend efficiency >= 0.50;
- close location >= 0.70;
- flow lead >= 0.04;
- absolute basis <= 25 bps.

At `t` retest:

- return is greater than -0.8%;
- low remains above 99% of the `t-1` open;
- close remains above the midpoint of the `t-1` range;
- taker imbalance >= 0.00;
- late/early volume ratio >= 1.0;
- absolute basis <= 25 bps.

## Ranking and portfolio rules

- Rank qualifying candidates by deterministic family score, then asset and family name.
- Select at most one candidate.
- Twelve-hour cooldown per asset/family.
- Six-hour primary cohorts may not overlap.
- No dynamic sizing or outcome-dependent selection.

## Costs and evaluation

- Primary horizon: six hours.
- Sensitivities: three and twelve hours.
- Standard round-trip cost: 20 bps.
- Stress round-trip cost: 40 bps.
- Entry and exit use next eligible hourly opens.
- Report gross/net compounded return, maximum drawdown, win rate, per-window returns, benchmarks, and asset/family contributions.

## Five-verification breakthrough gate

A `VERIFIED_FIVE_WINDOW_BREAKTHROUGH` requires every condition below:

1. overall six-hour standard net return > 0;
2. overall six-hour stress net return > 0;
3. each of the five verification windows has standard net return > 0;
4. each of the five verification windows has stress net return > 0;
5. at least three accepted events in each verification window;
6. at least 40 accepted events overall;
7. at least 25 active days overall;
8. at least two active families and four active assets;
9. maximum drawdown <= 8%;
10. overall standard return beats cash, the 15%-BTC benchmark, and the 15%-equal-weight benchmark;
11. at least one sensitivity has positive standard net return;
12. no asset contributes more than 50% of positive net contribution;
13. no family contributes more than 70% of positive net contribution;
14. all windows are complete with no excluded required hours.

Anything less is not a breakthrough. Positive discovery results alone do not count.

## Immutability

The protocol file, implementation fingerprint, input inventory, event inventory, report hash, and verification-window outcomes must be preserved. Any later hypothesis must receive a new version and new preregistration rather than editing v2.7 after seeing its verification results.
