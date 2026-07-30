# v2.3 Forward Alpha Candidate Protocol

## Objective

Develop a genuinely different paper-only alpha candidate in parallel with the frozen v2.1 router. The aim is repeatable positive net return after costs on future-only data, not another transformation of retired historical OHLCV windows.

## Immutable boundaries

- Paper research only; no orders, wallets, credentials, leverage, shorts or derivatives positions.
- `authorizes_trading=false` in every output.
- The frozen v2.1 router and v2.2 evaluation protocol remain untouched.
- Inputs are only completed append-only v2.0 forward snapshots.
- Decisions use information available at hour `t`; any simulated entry is no earlier than hour `t+1`.
- No v2.3 return may be calculated until implementation, fingerprints, costs, horizons and gates are committed.
- No threshold changes after outcome attachment begins.

## Frozen universe

BTC, ETH, SOL, AVAX, LINK and DOGE.

## Candidate families

Exactly three independent families are permitted.

### 1. Cross-venue dislocation normalization

Mechanism: temporary spot/perpetual dislocations accompanied by improving spot flow can normalize over the next several hours.

A long candidate requires all of:

- spot-perpetual basis is materially negative relative to its own trailing 24-hour distribution;
- basis has begun improving over the latest completed hour;
- spot taker imbalance and spot order-book imbalance are positive;
- perpetual flow is not stronger than spot flow;
- open interest is flat or contracting over six hours;
- current funding is non-positive or only mildly positive.

### 2. Liquidity-vacuum recovery

Mechanism: a sharp completed decline with contracting open interest and a thin sell-side book can produce a short-lived recovery when spot buyers return.

A long candidate requires all of:

- completed six-hour or 24-hour spot decline is extreme relative to the asset's trailing seven-day hourly distribution;
- open interest contracted materially during the decline;
- the latest completed hour is positive;
- spot taker imbalance and book imbalance have both turned positive;
- basis and funding are not deteriorating further;
- macro controls do not indicate a high-risk block.

### 3. Spot-led flow persistence

Mechanism: persistent spot demand that is not yet mirrored by derivatives positioning may continue briefly without the same crowding risk as perp-led momentum.

A long candidate requires all of:

- positive completed three-hour and six-hour spot return, but no extreme overextension;
- positive spot taker imbalance in at least two of the latest three completed hours;
- positive spot book imbalance now;
- spot flow exceeds perpetual flow by a frozen margin;
- basis, funding and open-interest growth remain bounded;
- broad risk controls permit exposure.

## Scoring and selection

- Each family emits a normalized score from only frozen conditions and robust trailing percentiles.
- At most one family is retained per asset.
- At most two assets are selected per hour.
- Ties are resolved deterministically by score, asset and family name.
- Seven-day return correlation at or above 0.85 removes the lower-ranked second asset.
- Maximum weight per asset is 20%.
- Maximum total exposure is 40%.
- At least 60% remains cash.
- Missing, stale or unverifiable factor families fail closed to cash.

## Anti-overfitting design

- Thresholds are expressed primarily as trailing within-asset percentiles rather than asset-specific constants.
- No grid search is permitted.
- Only one primary frozen configuration and two predetermined conservative ablations may be evaluated.
- Activity, family contribution and asset contribution must be reported; a single-family or single-asset result cannot pass.

## Frozen outcome protocol

- Entry reference: earliest valid spot mid in hour `t+1`.
- Primary exit: earliest valid spot mid in hour `t+5`, a four-hour hold.
- Sensitivity exits: two and eight hours.
- Standard round-trip cost: 20 basis points per selected position.
- Stress cost: 40 basis points.
- Overlapping hourly observations are allowed, but uncertainty uses UTC day-block resampling.

## Discovery and holdout

- Minimum 1,440 eligible hourly decisions.
- First 1,104 hours are discovery.
- Final 336 hours are an untouched holdout.
- Before 1,440 eligible hours, only readiness and activity diagnostics may be disclosed; no return, P&L, drawdown or benchmark performance.

## Discovery acceptance gate

Every condition must pass:

- at least 100 active decisions on at least 24 UTC days;
- at least four assets selected and all three families active;
- positive standard-cost and stress-cost compounded return;
- positive standard-cost return in both chronological halves;
- non-negative stress-cost return in both halves;
- positive lower bound of a 95% UTC day-block bootstrap interval for mean daily return;
- maximum drawdown no greater than 10%;
- beats cash, equal-weight universe, selected-assets passive and BTC benchmarks under standard costs;
- beats equal-weight universe under stress costs;
- at least four of six leave-one-asset-out runs positive under standard costs;
- at least two of three leave-one-family-out runs positive;
- no asset contributes more than 45% and no family more than 55% of positive P&L;
- primary four-hour horizon positive and at least one adjacent horizon positive.

## Untouched holdout gate

The final 336 hours are opened once only after discovery passes. Holdout requires:

- at least 24 active decisions on at least seven UTC days;
- positive standard-cost compounded return;
- non-negative stress-cost compounded return;
- return above equal-weight universe under standard costs;
- drawdown no greater than 10%;
- no single day contributes more than 50% of positive P&L.

Passing authorizes only a later time-limited shadow-paper observation design. It does not authorize continuous paper positions or real-money trading.

## Implementation sequence

1. Commit this protocol before implementation or outcome access.
2. Implement the deterministic decision-only router and its frozen fingerprints.
3. Verify it on synthetic fixtures and ordinary repository CI.
4. Collect canonical v2.3 decisions from append-only forward snapshots without calculating returns.
5. Implement readiness-only verification before any outcome attachment.
6. Attach outcomes only after 1,440 eligible decisions exist and permanently record discovery before opening the holdout.

The mechanism thresholds, universe, cost model, horizons and gates above are now frozen for the first v2.3 forward campaign. Later wording-only or workflow-transport corrections may not change them.
