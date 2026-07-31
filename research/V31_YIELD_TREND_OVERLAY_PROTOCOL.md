# v3.1 Yield-Bearing Cash and Trend Overlay Protocol

Status: frozen before implementation and before any v3.1 verification outcome access.

## Motivation

The v2.8 and v2.9 studies showed that low-turnover trend rotation can add value in sustained regimes, but event recovery and weak-regime entries repeatedly lost money. v3.0 confirmed that short-horizon recovery was negative when trend was absent. v3.1 therefore stops forcing crypto exposure in weak regimes. It holds a historically observable short-term Treasury cash return and adds only a small BTC/ETH trend overlay when completed daily evidence supports it.

The cash return is not treated as alpha. The report separately measures the strategy against the identical yield-bearing cash benchmark, and breakthrough status requires overall excess return above cash after costs.

## Safety and isolation

- Historical paper research only; long-or-cash.
- No leverage, shorts, derivatives positions, credentials, wallets, staking, lending, order placement or execution authorization.
- Maximum crypto exposure is model-defined and never above 20%.
- Unallocated weight remains in the yield-bearing cash sleeve.
- No writes to `forward-data/v2`.
- Track A and all v2.3-v3.0 sources, protocols, evidence, results and gates remain unchanged.
- Reports set `authorizes_trading=false`, `authorizes_shadow_paper=false`, `changes_track_a=false`, and `cannot_replace_forward_evidence=true`.

## Data sources

### Crypto

Public Binance completed daily spot klines for BTCUSDT and ETHUSDT.

Required dates: January 1, 2017 through January 1, 2026. Every archive URL and SHA-256 is recorded. Missing required bars fail closed and are never interpolated.

### Cash yield

Public FRED graph CSV series `DGS3MO` (3-Month Treasury Bill Secondary Market Rate), downloaded without credentials.

- The raw CSV URL and SHA-256 are recorded.
- Missing observations are not interpolated forward from the future.
- For crypto day `d`, the cash rate is the latest published non-missing observation dated on or before `d-1`.
- Before the first available observation, the experiment fails closed.
- Negative annualized rates, if present, are preserved rather than floored.
- Daily cash return is `(1 + annual_rate_decimal) ** (1/365) - 1`.
- The same cash return applies to the strategy cash sleeve and the pure-cash benchmark.

## Chronology and accounting

- Features for trading day `d` use completed crypto bars through `d-1` only.
- Targets fill at the open of `d`.
- Crypto return is measured open `d` to open `d+1`.
- Cash yield for that holding day uses only the latest rate known by `d-1`.
- Every discovery quarter and verification year starts and ends with zero crypto exposure.
- Final crypto liquidation is charged at the next open.
- Standard crypto round-trip cost: 20 bps.
- Stress crypto round-trip cost: 40 bps.
- One-way crypto turnover cost is half the round-trip rate times absolute crypto weight change.
- Cash sleeve accrual has zero simulated trading cost.
- Natural crypto weight drift is preserved until the next explicitly costed target change or final liquidation.

## Assets

Only BTC and ETH are eligible for the overlay. This avoids asset-list survivorship caused by later-listed altcoins during the 2017-2020 discovery period.

## Discovery and five independent verification years

### Discovery

January 1, 2017 through December 31, 2020.

The first 250 completed calendar days are feature warm-up. Every complete calendar quarter after warm-up is a discovery block. Only discovery blocks may select the model.

### Five verification years

1. `2021`
2. `2022`
3. `2023`
4. `2024`
5. `2025`

Each year is scored independently and cannot rescue another year.

## Fixed 64-model grid

The grid is the Cartesian product of:

- trend SMA length: 100 or 200 days;
- scheduled rebalance cadence: 5 or 10 observations;
- selected assets: top 1 or top 2;
- maximum crypto exposure: 10% or 20%;
- volatility target: 2% or 3% daily volatility;
- BTC 20-day drawdown brake: 10% or 20%.

Exactly 64 models. No model may be added after verification access.

## Features

Computed from completed closes ending on `d-1`:

- 1-, 5-, 20-, 60-, 120- and 200-day returns;
- SMA 50, 100 and 200;
- 20-day realized daily volatility;
- 20-day drawdown from trailing high.

Trend score:

`(0.50 * return_20 + 0.30 * return_60 + 0.20 * return_120) / max(volatility_20, 1.5%)`.

## Risk-on regime

Risk-on requires:

- BTC close above the selected SMA;
- BTC 60- and 120-day returns positive;
- at least one of BTC or ETH has close above the selected SMA plus positive 60- and 120-day returns.

Eligible assets require:

- close above the selected SMA;
- positive 20-, 60-, 120- and 200-day returns;
- positive trend score;
- one-day return no greater than +8%;
- five-day return no greater than +20%.

At the configured cadence, rank by trend score descending then asset name. Select top one or two.

## Exposure scaling

Start with model maximum exposure. Multiply by:

`min(1, model_volatility_target / median(selected 20-day volatility))`.

If BTC 20-day drawdown is at or below the model brake threshold, multiply exposure by 0.5.

Final crypto exposure is clamped between 0% and the model maximum, never above 20%. Selected assets receive equal weights. Remaining weight earns the cash rate.

When risk-on is false or no asset is eligible, crypto exposure is zero and the entire portfolio earns the cash rate.

## Discovery-only model selection

Evaluate all 64 models independently on every completed discovery quarter under 40-bps stress crypto costs. Each quarter starts and ends with zero crypto exposure.

Sort lexicographically by:

1. positive discovery quarters, descending;
2. quarters beating the identical cash benchmark, descending;
3. minimum excess quarter return over cash, descending;
4. median excess quarter return over cash, descending;
5. compounded stress excess return over cash, descending;
6. maximum drawdown, ascending;
7. crypto turnover, ascending;
8. deterministic model ID ascending.

Freeze the first model before verification evaluation.

## Discovery robustness gates

The chosen model must have:

- at least 10 positive discovery quarters;
- at least 8 discovery quarters beating cash;
- positive median excess quarter return over cash;
- positive compounded standard and stress return;
- positive compounded stress excess return over cash;
- minimum stress quarter return greater than -3%;
- stress maximum drawdown at most 8%.

## Five-year breakthrough gate

`VERIFIED_FIVE_YEAR_YIELD_TREND_BREAKTHROUGH` requires all conditions:

1. all discovery robustness gates pass;
2. each of the five verification years has positive standard absolute return;
3. each of the five verification years has positive stress absolute return;
4. overall standard and stress returns exceed the identical pure-cash benchmark;
5. at least three verification years beat cash under standard cost;
6. at least three verification years beat cash under stress cost;
7. each verification year has at least two costed crypto entry or rebalance days;
8. at least 20 costed crypto action days occur overall;
9. both BTC and ETH are selected at least once across verification;
10. verification maximum drawdown is at most 8%;
11. no asset supplies more than 80% of positive crypto contribution;
12. no verification year supplies more than 45% of positive excess contribution;
13. all required crypto bars and cash-rate observations are complete.

Anything less is `NOT_VERIFIED_FIVE_YEAR_YIELD_TREND_BREAKTHROUGH`.

A pass means five positive historical annual replications plus positive aggregate excess return over the same cash yield. It remains historical paper evidence only and does not authorize trading.

## Immutability

After this protocol commit, periods, assets, source series, model grid, feature formulas, cash accrual, costs, fills, selection and gates cannot change in v3.1. Any later attempt requires a new version and protocol.