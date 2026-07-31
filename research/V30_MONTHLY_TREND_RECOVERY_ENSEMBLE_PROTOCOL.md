# v3.0 Monthly Trend-Recovery Ensemble Protocol

Status: frozen before implementation and before v3.0 verification outcome access.

## Motivation

v2.8 showed that low-turnover trend rotation can produce strong overall returns but failed two of five quarters. v2.9 reduced drawdown and remained net positive, but still had two negative quarters and one inactive quarter. v3.0 tests whether a small trend sleeve plus a separately defined daily capitulation-recovery sleeve and a month-level loss brake can produce positive results consistently rather than relying on one strong quarter.

## Safety and isolation

- Historical paper research only; long-or-cash.
- No leverage, shorts, derivatives, credentials, wallets or order placement.
- Maximum exposure is 20%; minimum cash is 80%.
- Maximum two assets.
- No writes to `forward-data/v2`.
- Track A and all v2.3-v2.9 protocols, sources, evidence, results and gates remain unchanged.
- Reports set all trading/shadow authorization flags false and cannot replace forward evidence.

## Data and chronology

Use public Binance completed daily spot klines for BTC, ETH, SOL, AVAX, LINK and DOGE from January 1, 2021 through July 1, 2026. Record every URL and SHA-256. Missing bars fail closed.

For day `d`, features use completed bars through `d-1`; targets fill at open `d`; return is open `d` to open `d+1`. Every discovery month and verification month starts and ends in cash, with terminal liquidation cost charged.

Standard round-trip cost: 20 bps. Stress: 40 bps. One-way turnover costs half the round-trip rate.

## Discovery and five independent verification months

Discovery runs from January 1, 2021 through January 31, 2026. The first 200 days are warm-up. All complete calendar months after warm-up are discovery blocks.

Five untouched verification months:

1. `2026-02`
2. `2026-03`
3. `2026-04`
4. `2026-05`
5. `2026-06`

Each month must pass separately.

## Fixed 64-model grid

Cartesian product:

- trend SMA: 50 or 100 days;
- trend rebalance: 5 or 10 days;
- trend selected assets: top 1 or top 2;
- maximum exposure: 10% or 20%;
- five-day capitulation threshold: -8% or -12%;
- recovery holding period: 2 or 4 days.

Exactly 64 models; no additions after verification access.

## Features

From completed bars ending `d-1`:

- 1-, 3-, 5-, 10-, 20-, 60-, 120- and 180-day returns;
- SMA 20, 50, 100 and 200;
- 20-day realized volatility;
- 20-day drawdown from trailing high;
- close location;
- quote-volume ratio versus trailing 20-day median.

Trend score:

`(0.50 * return_20 + 0.30 * return_60 + 0.20 * return_120) / max(volatility_20, 2%)`.

## Trend sleeve

Trend mode requires BTC close above the model SMA, BTC 20- and 60-day returns positive, and at least one-third of assets with close above the model SMA plus positive 20- and 60-day returns.

Eligible assets additionally require positive 120- and 180-day returns, positive trend score, one-day return <= +8% and five-day return <= +20%.

At the configured cadence rank by trend score, with a 15% bonus for a controlled three-day pullback between -8% and -1% followed by positive one-day return. Allocate equally to top one or two using model maximum exposure, scaled by `min(1, 2.5% / median selected 20-day volatility)`.

## Capitulation-recovery sleeve

Evaluated only when trend mode is false and no recovery position is already active.

An asset qualifies when:

- five-day return <= the model threshold;
- 20-day drawdown <= 1.25 times the model threshold;
- one-day return between +1% and +8%;
- close location >= 60%;
- quote-volume ratio >= 1.20;
- close above 60% of SMA 200;
- BTC five-day return > -20%.

Rank by `one_day_return + abs(five_day_return) + 0.20 * max(0, volume_ratio - 1)`. Hold the top one or two for the fixed 2- or 4-day period at equal weights totaling the model maximum exposure. A recovery position exits at the next open after its fixed holding period; it is not extended by repeated signals.

## Defensive BTC core

When neither trend nor recovery qualifies, hold 5% BTC only when BTC close exceeds SMA 200, 20-day return is positive and one-day return is between -4% and +4%. Otherwise cash.

## Monthly loss brake

Within each independent month, if cumulative net strategy return from that month’s start reaches -1.5% or worse, all new risk is blocked and the portfolio remains cash through month end. Existing recovery holdings exit at the next eligible open before the brake stays active. The brake uses only prior realized strategy returns.

## Discovery model selection

Evaluate each model on every discovery month at 40-bps stress cost. Sort lexicographically by:

1. positive discovery months descending;
2. minimum month return descending;
3. median month return descending;
4. compounded stress return descending;
5. maximum drawdown ascending;
6. turnover ascending;
7. model ID ascending.

Freeze the first model before verification.

Discovery robustness requires:

- at least 32 positive discovery months;
- positive median month;
- positive compounded standard and stress returns;
- minimum stress month > -3%;
- stress drawdown <= 8%.

## Five-month breakthrough gate

`VERIFIED_FIVE_MONTH_ENSEMBLE_BREAKTHROUGH` requires:

1. discovery robustness passes;
2. overall verification standard and stress returns > 0;
3. all five verification months standard return > 0;
4. all five verification months stress return > 0;
5. at least two non-cash entry/rebalance days in every month;
6. at least 15 action days overall;
7. at least four assets selected;
8. both trend and recovery sleeves active across verification;
9. maximum drawdown <= 5%;
10. overall standard return beats cash, the model-exposure BTC benchmark and equal-weight benchmark;
11. no asset contributes >55% of positive contribution;
12. no month contributes >30% of positive contribution;
13. all required bars complete.

Anything less is `NOT_VERIFIED_FIVE_MONTH_ENSEMBLE_BREAKTHROUGH`.

## Immutability

Periods, grid, features, sleeves, brake, costs, fills and gates cannot change after this commit. Any later attempt uses a new version and new verification design.