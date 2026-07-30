# Crypto Multi-Source Holdout v1.3 Protocol

This is a one-shot paper-only evaluation on a previously barred crypto interval. It cannot authorise real-money trading or continuous paper trading.

## Motivation

The v1.2 independent-universe discovery showed that additional OHLCV-derived controls reduced losses but did not create positive edge. v1.3 therefore introduces genuinely independent market information rather than another price transformation:

1. blockchain activity from Coin Metrics Community archives;
2. USDT and USDC market-cap liquidity from Coin Metrics Community archives;
3. perpetual-futures funding from Bybit public market data; and
4. VIX, broad-dollar and US ten-year Treasury market-risk series from FRED public CSV downloads.

The source APIs, available columns and date coverage were inspected before this protocol. No price return from the barred interval was calculated.

## Frozen universe and time split

- LTCUSDT, BCHUSDT, LINKUSDT, XLMUSDT, ETCUSDT, ATOMUSDT, UNIUSDT and AAVEUSDT.
- Use the exact 2,050 aligned Coinbase daily histories already fingerprinted by v1.2.
- v1.3 evaluates only the first 180 candles of the previously barred 250-candle interval.
- Split those 180 candles into three consecutive non-overlapping 60-day test periods.
- The final 70 candles remain an embargo and are not evaluated.
- External-factor warm-up may use only dates before each test candle, including the final 180 discovery candles immediately preceding the barred interval.
- Signals and factor states use completed information; portfolio changes execute at the next Coinbase daily open.

## Frozen alpha layer

The underlying alpha remains the inherited simple-trend method:

- at least 60% positive 30/90-day trend breadth and above the 120-day average;
- positive median 90-day return;
- positive trend in the highest-dollar-volume market proxy;
- rank eligible assets by blended 30/90-day momentum divided by 30-day realised volatility;
- hold at most the strongest two assets with inherited inverse-volatility weights, 40% per-asset cap and 20% minimum cash reserve.

## Frozen independent factor families

### 1. Stablecoin liquidity

Combine daily USDT and USDC `CapMrktCurUSD`. The family is supportive only when both the 30-day and 90-day percentage changes are positive.

### 2. On-chain activity breadth

For each selected asset with Coin Metrics coverage, compare the latest 14-day averages of `AdrActCnt` and `TxCnt` with their preceding 90-day medians. The family is supportive when at least half of covered selected assets improve on both metrics. Assets with unavailable metrics are neutral, not failed.

### 3. Derivatives crowding

Aggregate each selected asset's Bybit settled funding rates into daily means. Compute the selected median seven-day mean and compare it with the preceding 90-day distribution. The family is supportive when the current value is no higher than the 75th percentile, avoiding excessively crowded long positioning.

### 4. Macro risk

Use completed daily FRED observations carried forward only after their observation date. The family is supportive when at least two of these conditions hold:

- VIX level no greater than 30 and its 20-observation change no greater than 25%;
- broad trade-weighted US dollar 20-observation change below 2%;
- US ten-year Treasury yield 20-observation increase below 0.50 percentage point.

## Frozen exposure mapping

- Fewer than two supportive families: cash.
- Exactly two supportive families: 50% of inherited raw trend target.
- Exactly three supportive families: 75% of inherited raw trend target.
- All four supportive families: preserve the inherited raw trend target.
- Existing prior-equity drawdown brake remains active and can only reduce exposure.
- External factors cannot create an entry, change the trend ranking, increase raw exposure or introduce leverage.

## Pre-registered comparisons

1. Primary four-family confirmation.
2. Remove stablecoin liquidity.
3. Remove on-chain activity.
4. Remove derivatives funding.
5. Remove macro risk.
6. Raw inherited simple trend.
7. Cash benchmark.

All portfolio transactions use inherited fees, slippage and estimated crypto taxes. An additional turnover-based cost stress remains applied.

## Data-integrity requirements

- Store SHA-256 fingerprints for Coinbase prices and every external source file.
- Preserve source URLs, retrieval timestamps, available date ranges and missing-value counts.
- Require all price histories, Bybit funding histories and macro series to cover the complete warm-up and test interval.
- Require Coin Metrics USDT and USDC liquidity coverage through the final test date.
- Require on-chain coverage for at least five of the eight portfolio assets; missing ATOM-style coverage remains neutral.
- Never backfill a value from a future observation date.

## Fail-closed holdout gate

The primary must:

- complete exactly three 60-day periods;
- have positive average, compounded and extra-cost-stressed return;
- make money in at least two of three periods;
- have positive average return over periods one and two and over periods two and three;
- beat raw simple trend on average and in at least two periods;
- keep worst drawdown no greater than 15%;
- trade in at least two periods and select at least three distinct assets;
- have at least three of the four leave-one-family-out variants produce positive average returns; and
- remain paper-only with no forward or live authorisation.

Passing authorises only a time-limited shadow-paper candidate and a new, later forward evaluation. It does not authorise continuous paper positions or real-money trading. This holdout is consumed once by v1.3; no threshold or factor may be changed after results are observed.
