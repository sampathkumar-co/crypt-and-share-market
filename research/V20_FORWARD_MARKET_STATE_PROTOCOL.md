# v2.0 Forward Crypto Market-State Data Protocol

## Status

Pre-registered before collector implementation and before any v2.x return calculation.

## Safety boundary

- Paper research only.
- Read-only public endpoints only.
- No exchange credentials, wallets, orders, leverage or live execution.
- This phase may collect and validate data, but it may not calculate strategy returns or unlock any historical holdout.

## Frozen universe and cadence

Assets: BTC, ETH, SOL, AVAX, LINK and DOGE.

Each snapshot has an exact UTC capture time and an hourly bucket. Repeated captures in the same bucket are preserved as distinct raw observations; no prior observation may be overwritten.

## Frozen public sources

1. Coinbase Exchange spot ticker, level-2 order book and recent public trades.
2. Hyperliquid public `metaAndAssetCtxs`, level-2 book and recent-trade information.
3. Coin Metrics Community daily CSV archives for USDT and USDC network/liquidity fields when available.
4. FRED daily VIX, broad-dollar and 10-year Treasury series.

Every source response is saved verbatim with URL/request metadata and SHA-256. Source failures remain explicit in the normalized record.

## Normalized fields

Per asset:

- spot mid, last price, spread and 24-hour volume;
- top-book bid/ask notional and order-book imbalance;
- recent Coinbase maker-side trade totals and derived taker-buy/taker-sell imbalance using Coinbase's documented maker-side convention;
- perpetual mark, oracle, funding, open interest and day notional volume;
- perpetual top-book spread and imbalance;
- spot-perpetual basis and cross-venue price dispersion;
- recent Hyperliquid reported-side trade imbalance;
- availability and validation flags for every factor family.

Global daily fields:

- USDT/USDC market-cap and adjusted-transfer values when published;
- VIX, broad-dollar index and 10-year Treasury yield;
- source observation dates and staleness in days.

Direct aggregate liquidation events are not fabricated. Until a verified public aggregate adapter is added, `liquidation_events` must be marked unavailable; later models may use only separately pre-registered proxies.

## Integrity rules

- Canonical JSON serialization and SHA-256 for every normalized snapshot.
- Raw response files are immutable and content-addressed.
- Manifest lists all raw and normalized hashes, source URLs, capture timestamps and errors.
- Existing snapshot IDs cannot be replaced with different content.
- Missing fields remain null with a reason; no silent interpolation.
- Non-finite or impossible prices, negative sizes and crossed non-auction books fail closed.
- The collector must be deterministic when supplied fixed responses and capture time.

## Promotion boundary

Successful collection proves only data-pipeline integrity. A separate v2.1 protocol must freeze the market-state router, code hash, data split, costs and gates before any strategy return is calculated.
