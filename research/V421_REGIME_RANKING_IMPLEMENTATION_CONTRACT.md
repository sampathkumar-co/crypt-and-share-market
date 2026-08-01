# v4.2.1 Regime-Ranking Implementation Contract

Status: frozen before the first v4.2 source inventory, model fit, or outcome is calculated.

## Exact daily source aggregation

For each asset and UTC calendar date:

- spot state is the Binance spot 1d kline whose open time is 00:00 UTC;
- perpetual state is the Binance USD-M perpetual 1d kline whose open time is 00:00 UTC;
- daily funding is the arithmetic sum of all funding observations timestamped within that UTC date;
- daily open interest is the final strictly positive `sum_open_interest` observation in that UTC date's metrics archive;
- spot and perpetual quote volume and taker-buy quote volume come directly from their respective daily kline;
- basis is perpetual close divided by spot close minus one;
- flow imbalance is twice taker-buy quote volume divided by quote volume minus one, clipped to [-1, 1].

Spot, perpetual, and funding monthly archives are mandatory. Missing daily metrics close only the affected asset-date. A model row exists only when all five assets have complete spot, perpetual, funding, and positive open-interest state for that date and enough lookback history.

## Source transport

- monthly spot, perpetual, and funding archives are downloaded concurrently;
- daily metrics archives are downloaded concurrently with a content-addressed local cache;
- static Binance 404 responses are recorded as missing source dates and are not retried indefinitely;
- other transport failures use three bounded attempts;
- every successful raw payload records URL and SHA-256;
- each report records successful and missing inventory counts and a canonical inventory SHA-256.

## Feature and label chronology

- a feature row at completed day D may use observations through D only;
- 3-day target is D+1 spot open to D+4 spot open;
- 7-day target is D+1 spot open to D+8 spot open;
- path downside uses spot lows from D+1 through D+3 relative to D+1 spot open;
- all cross-sectional ranks are calculated using only the five asset states on D;
- no verification-quarter observation may influence its fold's train or calibration data.

## Fixed regime labels

Regime labels use market-wide completed-day state:

- panic: next-three-day equal-weight market path drawdown is at most -3%;
- recovery: completed 30-day equal-weight return is below -8% and next-seven-day equal-weight return is positive;
- trend: completed 30-day equal-weight return is positive, completed breadth above the 100-day moving average is at least 60%, and next-seven-day equal-weight return is positive;
- otherwise chop.

These labels are training targets only. Production regime prediction uses completed features and never future observations.

## Specialist fallback

Each trend, recovery, and chop specialist is fit only on training rows bearing that regime. If a fold contains fewer than 250 specialist rows or fewer than two target classes, that specialist is unavailable and its regime must produce cash in calibration and verification. No cross-regime fallback is allowed.

## Fixed specialist score

For an eligible asset:

`0.60 * predicted_3d_return + 0.40 * predicted_7d_return + 0.25 * predicted_meta_probability - 0.35 * predicted_downside_probability - 0.50 * ensemble_disagreement`

Expected-return predictions are absolute next-open returns. Meta probability predicts top-two cross-sectional rank with positive 3-day stress-net return.

## Exact execution

- the signal for D is filled at D+1 spot open;
- a position is marked open-to-open each day using spot prices;
- scheduled target changes occur after exactly three complete open-to-open holding periods;
- predicted panic exits at the next open;
- target value is 5% of pre-trade portfolio equity per selected asset;
- actual absolute traded notional pays the one-way cost;
- natural drift is preserved between target changes;
- terminal liquidation pays cost but does not count as a target-changing decision;
- each verification fold starts from 100% cash and is scored independently.

## Frozen calibration score and tie break

Calibration maximizes:

`standard_net_return - 2 * maximum_drawdown - 0.25 * turnover`

Fewer than eight target-changing decisions receives a fixed penalty of one.

Ties are broken lexicographically by:

1. lower maximum drawdown;
2. lower turnover;
3. lower meta threshold;
4. lower maximum downside probability;
5. top one before top two;
6. lower learning rate;
7. fewer leaves.

## Safety

The campaign is paper-only. It never creates derivatives positions; derivatives data is informational. `authorizes_trading=false` and only an isolated fictional shadow ledger may set `authorizes_shadow_paper=true`.
