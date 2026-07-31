# v2.9 Risk-Scaled Trend Rotation Protocol

Status: frozen before implementation and before any v2.9 verification outcome access.

## Motivation

The preregistered v2.8 experiment generated positive overall verification returns after standard and stress costs, but failed the five-window requirement because 2024-Q2 and 2024-Q3 were negative. Its trend sleeve contributed positively, while its recovery and neutral sleeves contributed negatively. v2.9 removes those losing sleeves and tests whether a risk-scaled trend-only mechanism generalizes to five new quarters.

This is historical paper research only. It cannot authorize trading or modify Track A.

## Isolation and safety

- Long-or-cash and paper-only.
- No leverage, shorts, derivatives positions, credentials, wallets or order placement.
- Maximum gross exposure is model-defined but never above 30%.
- Maximum two assets.
- No write to `forward-data/v2`.
- v2.3-v2.8 sources, protocols, results, gates, holdouts and evidence remain unchanged.
- All reports set `authorizes_trading=false`, `authorizes_shadow_paper=false`, `changes_track_a=false`, and `cannot_replace_forward_evidence=true`.

## Data

Assets: BTC, ETH, SOL, AVAX, LINK and DOGE against USDT.

Use only public Binance completed daily spot kline archives. Record every URL and SHA-256. Required dates are January 1, 2021 through April 1, 2026. Missing bars fail closed; no interpolation is permitted.

## Chronology and accounting

- Features for trading day `d` use completed bars through `d-1` only.
- Target weights fill at the open of `d`.
- Return is measured from open `d` to open `d+1`.
- Each discovery quarter and verification quarter starts and ends in cash.
- The final liquidation at the next open is charged.
- Standard round-trip cost: 20 bps.
- Stress round-trip cost: 40 bps.
- One-way turnover cost is half the round-trip cost times absolute weight change.
- Carried weights are rescaled to the model's current exposure cap before each day’s accounting.

## Discovery and verification

Discovery: January 1, 2021 through December 31, 2024. The first 200 days are warm-up. All complete calendar quarters after warm-up are discovery blocks and may be used for deterministic model selection.

Five untouched verification quarters:

1. `2025-Q1`: January 1-March 31, 2025.
2. `2025-Q2`: April 1-June 30, 2025.
3. `2025-Q3`: July 1-September 30, 2025.
4. `2025-Q4`: October 1-December 31, 2025.
5. `2026-Q1`: January 1-March 31, 2026.

Each verification quarter is scored independently and cannot be rescued by aggregation.

## Fixed 64-model grid

The model grid is the Cartesian product of:

- trend SMA: 80 or 150 days;
- breadth floor: 33% or 50%;
- rebalance cadence: 5 or 10 observations;
- selected assets: top 1 or top 2;
- maximum exposure: 15% or 30%;
- BTC 20-day drawdown brake: 10% or 20%.

Exactly 64 models are evaluated. No model may be added after verification access.

## Features

Computed from completed data ending `d-1`:

- 1-, 3-, 5-, 20-, 60-, 120- and 180-day returns;
- 20-day realized volatility;
- 20-day BTC drawdown from its trailing high;
- SMA 50, 80, 150 and 200;
- daily close location and volume ratio versus trailing 20-day median.

Trend score:

`(0.45 * return_20 + 0.35 * return_60 + 0.20 * return_120) / max(volatility_20, 2%)`.

## Strong trend regime

Strong regime requires:

- BTC close above the model SMA;
- BTC 60- and 120-day returns positive;
- the model breadth fraction of assets have close above the model SMA and positive 60- and 120-day returns.

Eligible assets additionally require positive 20- and 180-day returns and positive trend score. Exclude an asset when its one-day return exceeds +8% or five-day return exceeds +20%.

Rank by trend score with a fixed 15% bonus for a controlled pullback: three-day return between -8% and -1% followed by positive one-day return.

At the configured cadence, allocate equally to the top one or two assets.

## Moderate BTC regime

When strong regime is false, a 10% BTC core is allowed only when:

- BTC close is above SMA 200;
- BTC 60- and 120-day returns are positive;
- at least one-third of assets have positive 60-day returns;
- BTC one-day return is between -5% and +5%.

Otherwise remain fully in cash.

## Risk scaling

For strong-regime selected assets:

- start with the model maximum exposure;
- multiply by `min(1, 2.5% / median selected 20-day daily volatility)`;
- if BTC 20-day drawdown is at or below the model brake threshold, multiply exposure by 0.5;
- exposure may never exceed the model maximum or 30%.

The moderate BTC core is fixed at 10% and is also halved by the drawdown brake.

## Discovery-only model selection

Evaluate all 64 models on each complete discovery quarter under 40-bps stress costs.

Order models lexicographically by:

1. number of positive discovery quarters, descending;
2. minimum discovery-quarter return, descending;
3. median discovery-quarter return, descending;
4. compounded discovery stress return, descending;
5. maximum drawdown, ascending;
6. turnover, ascending;
7. deterministic model ID ascending.

Freeze the first model before evaluating verification quarters.

Discovery robustness requires:

- at least 10 positive discovery quarters;
- positive median quarter;
- positive compounded standard and stress return;
- minimum stress quarter greater than -5%;
- stress maximum drawdown at most 10%.

## Five-quarter breakthrough gate

`VERIFIED_FIVE_QUARTER_TREND_BREAKTHROUGH` requires all conditions:

1. discovery robustness passes;
2. overall verification standard return > 0;
3. overall verification stress return > 0;
4. each of the five verification quarters has standard return > 0;
5. each of the five verification quarters has stress return > 0;
6. each quarter has at least three non-cash entry or rebalance days;
7. at least 25 non-cash action days overall;
8. at least four assets selected overall;
9. verification maximum drawdown <= 8%;
10. overall standard return beats cash, a same-period maximum-exposure BTC benchmark and same-exposure equal-weight benchmark;
11. no asset supplies more than 55% of positive contribution;
12. no quarter supplies more than 35% of positive contribution;
13. all required bars are complete.

Anything less is `NOT_VERIFIED_FIVE_QUARTER_TREND_BREAKTHROUGH`.

## Immutability

After this commit, periods, features, model grid, ranking, risk scaling, costs, fills and gates cannot change in v2.9. Later hypotheses require a new version and new verification periods. A pass remains historical evidence only.