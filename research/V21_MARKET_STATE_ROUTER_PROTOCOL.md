# v2.1 Forward Market-State Router Protocol

## Status

Pre-registered before router implementation and before any v2.1 return calculation.

## Claim and safety boundary

- Paper research only.
- The router emits research decisions, never orders.
- `authorizes_trading` is always false.
- No leverage, shorts, wallets, credentials or live execution.
- This phase must not calculate strategy profit, drawdown or Sharpe ratio.
- Historical OHLCV windows used by retired families are prohibited.

## Input contract

Input consists only of normalized v2.0 forward snapshots collected after the v2.0 merge.

- Use the earliest valid capture in each UTC hour bucket.
- Require 168 unique completed hourly buckets before any BUY candidate can appear.
- Every feature at hour `t` uses only buckets at or before `t`.
- Decisions are intended for observation at the following hourly cycle; this module does not simulate fills.
- Missing or invalid required factor families make the affected sleeve unavailable rather than silently imputing values.

## Frozen universe

BTC, ETH, SOL, AVAX, LINK and DOGE.

## Sleeve 1: capitulation-recovery proxy

This is explicitly a proxy, not direct liquidation data.

A long candidate requires all of:

- 24-hour spot-mid return no greater than -3.5%;
- 24-hour perpetual open-interest change no greater than -8%;
- current Coinbase-derived taker imbalance at least +0.15;
- current spot-book imbalance at least +0.05;
- current spot mid above the prior hourly spot mid;
- current basis improving by at least 2 basis points from the prior hour; and
- current funding no greater than +0.005% per funding observation.

## Sleeve 2: negative-basis normalization

A long candidate requires all of:

- prior-hour spot/perpetual basis no greater than -20 basis points;
- current basis improves by at least 5 basis points;
- current basis remains no greater than +5 basis points;
- 24-hour open-interest change no greater than -3%;
- current spot taker imbalance at least +0.10;
- current spot mid above the prior hour; and
- current funding no greater than zero.

## Sleeve 3: spot-led continuation

A long candidate requires all of:

- six-hour spot-mid return between +1.5% and +6%;
- current Coinbase-derived taker imbalance at least +0.20;
- current spot-book imbalance at least +0.10;
- spot taker imbalance exceeds Hyperliquid reported-side imbalance by at least 0.10;
- absolute spot/perpetual basis no greater than 15 basis points;
- six-hour open-interest change between -2% and +8%; and
- current funding no greater than +0.01% per funding observation.

## Global controls

- VIX above 35 blocks every sleeve when the observation is no more than seven days stale.
- A seven-day broad-dollar increase above 2% blocks every sleeve when both observations are available and no more than seven days stale.
- Missing or stale macro data does not create an entry; it caps total target exposure at 25%.
- Fresh combined USDT/USDC market-cap growth below -1% over seven days caps total target exposure at 25%.
- Missing or more-than-14-day-stale stablecoin data also caps total target exposure at 25%.
- Otherwise total target exposure is capped at 50%.
- At most two assets may be selected.
- Each selected asset is capped at 25%, so at least 50% cash is always retained.
- If selected assets have seven-day hourly return correlation of at least 0.85, keep only the higher-ranked asset.

## Frozen ranking

Each qualified sleeve produces one asset candidate. Candidate score is the sum of bounded, unitless components:

- capitulation recovery: downside magnitude / 3.5%, open-interest contraction / 8%, spot taker imbalance, spot-book imbalance, and basis recovery / 2 bps;
- basis normalization: prior negative-basis magnitude / 20 bps, basis recovery / 5 bps, open-interest contraction / 3%, and spot taker imbalance;
- spot-led continuation: six-hour return / 1.5%, spot taker imbalance, spot-book imbalance, spot-minus-perpetual flow lead, and unused basis capacity.

Every ratio component is capped at 3. If an asset qualifies for multiple sleeves, retain only its highest-scoring sleeve. Rank by score descending, then asset symbol ascending for deterministic ties.

## Output contract

The router writes canonical JSON containing:

- exact input snapshot IDs and record hashes;
- feature values and availability by asset;
- every sleeve condition and reason;
- macro/stablecoin controls;
- selected assets and research-only target weights;
- minimum cash weight;
- protocol and implementation fingerprints; and
- `paper_only=true`, `authorizes_trading=false`.

Insufficient history, stale controls, no candidates or invalid inputs must produce a valid cash decision rather than a fabricated trade.

## Future evaluation boundary

No performance gate exists in v2.1 because no genuinely forward outcome history exists yet. A later v2.2 evaluation protocol must be frozen before attaching future returns. It must require doubled-cost profitability, positive chronological halves, sufficient real activity, leave-one-asset and leave-one-factor robustness, passive-benchmark improvement, controlled drawdown and a final untouched interval or second venue.
