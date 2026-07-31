# v2.5 Historical Proxy Screen Protocol

## Status and purpose

This protocol is frozen before implementation and before downloading or evaluating
its historical outcome period. It defines Track B: a fast historical screening
exercise for the already-frozen v2.5 families.

Track B is not forward evidence, does not alter Track A, and cannot unlock any
v2.5 readiness, discovery, holdout, shadow-paper or trading state. Its outputs
must always be labelled `HISTORICAL_PROXY_SCREEN_ONLY`.

## Frozen period and universe

- Screening period: 2026-06-01 00:00 UTC through 2026-06-30 23:00 UTC.
- Warm-up data begins 2026-05-24 00:00 UTC.
- Universe: BTC, ETH, SOL, AVAX, LINK and DOGE against USDT.
- Data source: unauthenticated public Binance spot and USD-M futures market-data
  archives or public market-data endpoints only.
- The period, assets, thresholds, costs and mappings may not be changed after an
  outcome is observed in order to improve results.

## Isolation and safety

- Paper research only.
- No live orders, wallets, credentials, API secrets, leverage, shorts or borrowing.
- Track B writes only GitHub Actions artifacts; it must never write to
  `forward-data/v2` or any Track A evidence directory.
- Existing v2.3, v2.4 and v2.5 source, protocols, fingerprints, decisions,
  readiness and holdouts are read-only and unchanged.
- Track B may reject itself for insufficient data coverage, but may not silently
  fill missing hours or invent unavailable fields.

## Historical proxy mapping

The exact forward collector uses Coinbase spot quotes/books/trades and
Hyperliquid perpetual state/books/trades. Those historical top-ten books are not
available in the same normalized archive for this fixed period. Track B therefore
uses explicit Binance hourly proxies and records their provenance:

- `spot_mid`: spot hourly close.
- `spot_taker_imbalance`: `(2 * taker_buy_quote_volume / quote_volume) - 1`.
- `perp_flow_imbalance`: the same calculation from USD-M futures hourly klines.
- `funding`: most recent public funding rate known at or before the completed hour.
- `open_interest_base`: hourly open-interest quantity from public futures metrics.
- `basis_bps`: `(futures_close / spot_close - 1) * 10,000`.
- `spot_spread_bps`: hourly spot high-low range divided by the close, in bps.
- `spot_book_notional`: hourly spot quote volume.
- `spot_book_imbalance`: spot taker imbalance.

The last three substitutions are liquidity proxies, not historical top-ten order
book observations. Every report must disclose this limitation. Family 3 is
therefore exploratory-only even within Track B.

## Decision construction

- Build one normalized proxy snapshot per completed UTC hour.
- Require all six assets and every mapped field for an hour; otherwise exclude the
  hour and break continuity.
- Reuse the frozen `evaluate_forward_alpha_v25` router without changing its
  thresholds or portfolio rules.
- A decision at hour `t` uses only data available at or before `t`.
- Earliest simulated entry is the next hour's spot open (`t+1`).
- Repeated same-asset/same-family signals within four hours count as one event,
  retaining only the earliest event.
- Maximum two assets, 15% per asset, 30% total intended exposure, at least 70%
  cash, long-or-cash only.

## Evaluation

Evaluate each accepted event at 2-hour, 4-hour and 8-hour holding horizons using
next-hour spot open for entry and the corresponding future spot open for exit.

- Standard round-trip friction: 20 bps.
- Stress round-trip friction: 40 bps.
- Portfolio return uses the frozen target weights; unused capital remains cash.
- Overlapping event cohorts are permitted because each decision represents a
  separately timestamped paper cohort; event cooldown prevents immediate repeats.
- Report gross and net compounded return, maximum drawdown, event count, active
  days, win rate, family/asset contribution and benchmark returns.
- Benchmarks: cash, 30%-exposure BTC and 30%-exposure equal-weight universe over
  the same eligible entry/exit timestamps.

## Screening interpretation

Track B is encouraging only when the primary 4-hour screen has:

- positive net return after 20 bps costs;
- non-negative net return after 40 bps costs;
- at least 20 accepted events on at least 7 UTC days;
- maximum drawdown no greater than 10%;
- improvement over cash and both 30%-exposure benchmarks;
- positive results in both chronological halves;
- at least two active families and three active assets.

Failure is informative but does not alter Track A. Passing is also only a
historical clue and cannot reduce the 169-hour Track A warm-up or the 1,448-hour
sealed forward evaluation requirement.

## Reproducibility

The output must include:

- protocol and implementation SHA-256 fingerprints;
- fixed dates and asset list;
- every downloaded URL and byte hash;
- excluded hours and reasons;
- proxy mapping disclosure;
- deterministic report SHA-256;
- `paper_only=true`, `authorizes_trading=false`, and
  `authorizes_shadow_paper=false`.
