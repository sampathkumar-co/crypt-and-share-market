# v2.6 Cost-Aware Historical Discovery Protocol

## Status

This protocol is frozen before v2.6 implementation and before any v2.6 historical outcome is calculated. It is an isolated historical research track created in response to the v2.5 Track B result. It does not alter the frozen v2.5 forward campaign, its snapshots, decisions, readiness, gates or holdout.

## Objective

Find out whether stronger event confirmation, lower turnover and richer intrahour proxies can produce positive net paper returns after realistic costs. The aim is not to rescue the June 2026 screen by tuning to its fifteen events.

## Immutable safety boundary

- Historical research only; `authorizes_trading=false` and `authorizes_shadow_paper=false`.
- Long-or-cash only; no shorts, leverage, borrowing, derivatives orders, credentials or wallets.
- Never write to `forward-data/v2`.
- Never change v2.5 source, protocol, thresholds, evidence or fingerprints.
- Completed bars only. Signal hour `t`, confirmation hour `t+1`, earliest entry at the open of `t+2`.
- Maximum one selected asset per event and 15% intended portfolio weight.
- Minimum 85% cash.

## Public historical inputs

Use only fixed public Binance Vision archives, with every downloaded ZIP recorded by URL and SHA-256:

- spot 5-minute klines;
- USD-M perpetual 5-minute klines;
- USD-M funding-rate archives;
- USD-M daily open-interest metrics.

No order-book history is claimed. Intrahour liquidity features are explicitly historical proxies.

## Frozen evaluation windows

Each window has 8 days of warm-up and 30 calendar days of scored decisions:

1. 2025-08-01 through 2025-08-30 UTC;
2. 2025-11-01 through 2025-11-30 UTC;
3. 2026-02-01 through 2026-03-02 UTC;
4. 2026-05-01 through 2026-05-30 UTC.

Windows 1 and 2 form discovery. Windows 3 and 4 are untouched validation windows. Missing required hours or archives fail closed and exclude the affected window; they may never be synthesized.

## Intrahour features

Aggregate completed 5-minute bars into hourly state. For spot and perpetual markets calculate:

- open, high, low, close and quote volume;
- taker-buy imbalance;
- trend efficiency: absolute hourly return divided by summed absolute 5-minute returns;
- close-location value within the hourly range;
- maximum 5-minute volume share;
- last-three-versus-first-three 5-minute volume ratio;
- realized 5-minute volatility;
- spot/perpetual flow lead;
- spot/perpetual basis;
- latest known funding and hourly open interest.

## Family A: confirmed residual continuation

For each non-BTC asset, estimate beta from the prior 168 completed hourly returns. At signal hour `t` require:

- six-hour residual return at least 1.2%;
- 24-hour residual return at least 2.0%;
- both residual measures at or above their trailing 85th percentiles;
- six-hour raw return positive and no greater than 8%;
- spot taker imbalance at least 0.12;
- spot-perpetual flow lead at least 0.08;
- spot trend efficiency at least 0.45;
- close-location value at least 0.60;
- absolute basis no greater than 20 bps;
- funding no greater than 0.010%;
- six-hour open-interest growth between -4% and +6%.

At confirmation hour `t+1` require positive one-hour return, positive residual return, spot taker imbalance at least 0.08, trend efficiency at least 0.40 and no more than 50% retracement of the signal-hour move.

## Family B: confirmed derivatives unwind recovery

At signal hour `t` require:

- funding at or below its trailing 10th percentile or basis at or below its trailing 10th percentile and below zero;
- six-hour open-interest contraction at least 3%;
- current basis improves at least 3 bps from the prior hour;
- spot taker imbalance at least 0.08;
- spot-perpetual flow lead at least 0.05;
- positive close-location value.

At confirmation hour `t+1` require positive spot return, further non-negative basis change, funding no worse than at `t`, spot taker imbalance at least 0.10 and trend efficiency at least 0.35.

## Family C: intrahour sweep-replenishment continuation

At signal hour `t` require the prior hour to show a liquidity shock:

- range at or above its trailing 85th percentile;
- maximum 5-minute volume share at or above its trailing 85th percentile;
- trend efficiency at least 0.50.

The current signal hour must show:

- range contraction of at least 20%;
- last-three-versus-first-three volume ratio at least 1.10;
- spot taker imbalance at least 0.12;
- close-location value at least 0.60;
- positive one-hour return no greater than 4%;
- spot-perpetual flow lead at least 0.08;
- absolute basis no greater than 20 bps.

At confirmation hour `t+1` require positive return, taker imbalance at least 0.08, close-location at least 0.55 and no renewed range expansion above the signal hour.

## Cost-aware selection

- A candidate must have signal amplitude at least 1.0%, five times the standard 20 bps round-trip cost.
- Rank by deterministic family score, then asset and family.
- Select at most one asset.
- Repeated events for the same asset and family are suppressed for 8 hours.
- A second event cannot overlap an existing four-hour primary cohort.

## Fills, horizons and costs

- Earliest entry: open of `t+2`.
- Primary exit: open four hours after entry.
- Sensitivities: two and eight hours.
- Standard round-trip cost: 20 bps.
- Stress round-trip cost: 40 bps.
- Portfolio contribution equals 15% weight times asset return after costs.

## Acceptance criteria

The implementation is encouraging only when all are true:

- aggregate four-hour standard-cost return is positive;
- aggregate four-hour stress-cost return is non-negative;
- both discovery windows combined are positive after standard costs;
- each validation window is independently positive after standard costs;
- at least 30 accepted events on at least 20 UTC days;
- at least two families and four assets are active;
- maximum drawdown is no greater than 8%;
- four-hour result beats cash, 15%-exposure BTC and 15%-exposure equal-weight universe;
- at least one of the two-hour or eight-hour sensitivities is positive after standard costs;
- no single asset contributes more than 50% and no family more than 65% of positive P&L.

Failure is informative and must remain visible. No result from this track can shorten or replace Track A forward evidence.
