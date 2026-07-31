# v2.8 Regime-Adaptive Rotation Protocol

Status: frozen before implementation and before v2.8 verification outcome access.

## Purpose

The event-driven v2.5-v2.7 mechanisms produced sparse entries and negative post-entry continuation. v2.8 therefore changes both timescale and mechanism. It tests a low-turnover daily long-or-cash portfolio that rotates toward persistent relative strength in healthy regimes and uses a separately defined capitulation-recovery sleeve when the trend regime is unhealthy.

A result may be called a five-window historical breakthrough candidate only if every fixed verification quarter is profitable after both standard and stress costs and every gate below passes. It remains historical evidence and cannot authorize trading or alter Track A.

## Isolation and safety

- Historical research only.
- Paper-only and long-or-cash.
- No live orders, wallets, credentials, leverage, shorts, derivatives positions or execution authorization.
- Maximum total exposure: 30%.
- Minimum cash weight: 70%.
- Maximum two assets at once.
- No writes to `forward-data/v2`.
- No modification of v2.3-v2.7 source, protocols, results, profitability gates, selection-stability gates or Track A evidence.
- Protocol, implementation, chosen model, source archives and report are SHA-256 inventoried.

## Assets and data

Assets: BTC, ETH, SOL, AVAX, LINK and DOGE against USDT.

Only public Binance spot daily kline archives are used. Each daily signal uses completed bars only. Missing required bars fail the affected period closed; prices are never interpolated.

The required history starts January 1, 2021 and ends January 1, 2025, including the final open needed to close December 31, 2024 exposure.

## Chronology and fills

- Features for date `d` use closes through `d-1` only.
- Target weights determined from those completed features fill at the open of `d`.
- Daily portfolio return is measured from the open of `d` to the open of `d+1`.
- Rebalance costs are charged at the open where weights change.
- Standard round-trip cost: 20 bps.
- Stress round-trip cost: 40 bps.
- A one-way weight change is charged half the round-trip rate times absolute portfolio turnover.
- No close from `d` or later may influence the open-of-`d` allocation.

## Discovery and verification split

### Discovery interval

January 1, 2021 through September 30, 2023.

The first 200 completed days are feature warm-up. The remaining discovery observations are split into consecutive non-overlapping 90-day blocks. Only discovery blocks may select the model.

### Five fixed verification windows

1. `2023-Q4`: October 1-December 31, 2023.
2. `2024-Q1`: January 1-March 31, 2024.
3. `2024-Q2`: April 1-June 30, 2024.
4. `2024-Q3`: July 1-September 30, 2024.
5. `2024-Q4`: October 1-December 31, 2024.

The model-selection procedure and complete finite model grid are frozen before any v2.8 verification result is read. Verification quarters are reported separately and cannot rescue one another.

## Fixed finite model grid

Every model is the Cartesian product of:

- trend SMA length: 80 or 120 days;
- trend breadth floor: 40% or 60%;
- scheduled rebalance cadence: every 5 or 7 calendar observations;
- trend sleeve selection: top 1 or top 2 assets;
- recovery five-day drawdown threshold: -6% or -10%.

There are exactly 32 models. No other model or threshold may be added after verification access.

## Shared features

For every asset, computed from completed closes ending on `d-1`:

- 1-, 3-, 5-, 20-, 60- and 120-day returns;
- 20-day realized volatility;
- simple moving averages for 50, 80, 100 and 120 days;
- close location within the completed daily range;
- quote-volume ratio versus the trailing 20-day median.

A trend score is:

`(0.45 * return_20 + 0.35 * return_60 + 0.20 * return_120) / max(volatility_20, 2%)`.

## Sleeve A: cross-sectional trend rotation

The trend sleeve is active when:

- BTC close exceeds the selected SMA length;
- BTC 20-day and 60-day returns are positive;
- at least the selected breadth floor of assets have close above the selected SMA, positive 20-day return and positive 60-day return.

Eligible assets must additionally have positive 120-day return and positive trend score. Assets are ranked by:

1. trend score descending;
2. a fixed 15% score bonus when the asset has a controlled three-day pullback between -6% and -1% followed by a positive one-day return;
3. asset name ascending.

At each scheduled rebalance, the top one or two eligible assets receive equal weights totaling 30%. If none qualify, the portfolio remains in cash.

## Sleeve B: capitulation recovery

This sleeve is evaluated daily only when the trend sleeve is inactive.

An asset qualifies when:

- its five-day return is at or below the model's fixed recovery threshold;
- its completed one-day return is at least +1.5%;
- its close location is at least 60%;
- its quote-volume ratio is at least 1.20;
- its 120-day return is greater than -35%;
- BTC five-day return is greater than -18%.

Qualifying assets are ranked by recovery strength:

`one_day_return + abs(five_day_return) + 0.25 * max(0, volume_ratio - 1)`.

The top two receive equal weights totaling 30%. Recovery positions are recomputed daily and cannot persist without the signal.

## Sleeve C: neutral-regime leader

When neither trend nor recovery is active, a single 15% position is allowed only if:

- BTC 20-day return is greater than -5%;
- at least one-third of assets have positive 20-day return;
- the selected asset has positive 20-, 60- and 120-day returns;
- close exceeds its 50-day SMA;
- one-day return is between -2% and +3%;
- trend score is positive.

The highest trend score is selected. Otherwise remain fully in cash.

## Discovery-only model selection

Each of the 32 models is evaluated on every completed discovery block under 40-bps stress cost.

Models are ordered lexicographically by:

1. number of positive discovery blocks, descending;
2. minimum discovery-block return, descending;
3. median discovery-block return, descending;
4. compounded stress return, descending;
5. maximum drawdown, ascending;
6. turnover, ascending;
7. deterministic model identifier ascending.

The first model is frozen as `chosen_model` before verification evaluation. The chosen model and selection table are included in the report.

A model need not pass discovery gates to be selected, but failure is recorded and prevents breakthrough status.

## Discovery robustness gates

The chosen model must have:

- at least six positive discovery blocks;
- positive median discovery-block return;
- positive compounded standard and stress discovery return;
- minimum discovery-block stress return greater than -4%;
- discovery maximum drawdown at most 10%.

## Five-window breakthrough gate

`FIVE_QUARTER_ROTATION_BREAKTHROUGH_CANDIDATE` requires every condition:

1. all discovery robustness gates pass;
2. overall verification standard net return > 0;
3. overall verification stress net return > 0;
4. each of the five verification quarters has standard net return > 0;
5. each of the five verification quarters has stress net return > 0;
6. each verification quarter has at least four non-cash entry or rebalance days;
7. at least 30 non-cash entry or rebalance days occur across verification;
8. at least four assets are selected across verification;
9. at least two sleeves are active across verification;
10. verification maximum drawdown <= 8%;
11. verification standard return beats cash, the 30%-BTC benchmark and the 30%-equal-weight benchmark;
12. no asset supplies more than 55% of positive verification contribution;
13. no quarter supplies more than 40% of positive verification contribution;
14. every required daily bar is present and all five windows are complete.

Anything less is `NOT_FIVE_QUARTER_VERIFIED`.

## Immutability

After this protocol commit, model definitions, grid, feature formulas, model selection, costs, fills, fixed periods and gates cannot change in v2.8. Any later mechanism must use a new version and new verification windows. A passing result is still historical evidence only and sets `authorizes_trading=false`, `authorizes_shadow_paper=false`, `changes_track_a=false`, and `cannot_replace_forward_evidence=true`.
