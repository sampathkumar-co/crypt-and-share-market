# Crypto Multi-Regime 4-Hour Discovery v1.4

## Status

This protocol is frozen before any v1.4 strategy return is calculated.

The experiment is paper-only, long-or-cash, unlevered and cannot authorise real-money trading. A successful result may only unlock a one-shot final holdout and, after that, a time-limited shadow-paper candidate.

## Why this experiment exists

The v1.3.1 daily multi-source holdout preserved capital but every strategy arm, including raw daily trend, stayed in cash for all three periods. The next experiment therefore changes the opportunity model rather than retuning the rejected daily gate.

v1.4 tests three distinct 4-hour alpha sleeves:

1. trend pullback or continuation;
2. range mean reversion;
3. negative-funding dislocation rebound.

A frozen router chooses among them by regime. External data may reduce exposure or activate the funding-rebound sleeve, but it cannot create arbitrary entries or increase exposure above the raw alpha target.

## Asset-disjoint universe

The required Coinbase spot products are:

- AVAX-USD
- DOT-USD
- NEAR-USD
- FIL-USD
- ICP-USD
- OP-USD
- ARB-USD
- SUI-USD

These assets are disjoint from the earlier five-asset v0.x universe and the eight-asset v1.2/v1.3 universe.

The matching internal symbols are AVAXUSDT, DOTUSDT, NEARUSDT, FILUSDT, ICPUSDT, OPUSDT, ARBUSDT and SUIUSDT.

## Frozen market-data interval

- Source: Coinbase Exchange public read-only candles.
- Fetch granularity: 1 hour.
- Aggregate to UTC-aligned 4-hour candles: 00:00, 04:00, 08:00, 12:00, 16:00 and 20:00 UTC.
- Start inclusive: 2023-06-07T00:00:00Z.
- End exclusive: 2025-11-23T00:00:00Z.
- Required aligned result: exactly 5,400 complete 4-hour candles per asset.
- Any missing hourly component invalidates its 4-hour candle. The workflow fails if the common aligned set is not exactly 5,400 bars.

No price data dated 2025-11-23 or later may be used. This keeps the consumed v1.3 test and its final 70-day embargo outside v1.4.

## Frozen split

The 5,400 aligned 4-hour bars are divided as follows:

- warm-up: first 600 bars;
- discovery evaluation: eight consecutive, non-overlapping periods of 480 bars each;
- pre-holdout embargo: 240 bars;
- barred final holdout: last 720 bars.

Index boundaries:

- warm-up: 0-599;
- discovery periods: 600-4,439;
- embargo: 4,440-4,679;
- final holdout: 4,680-5,399.

Approximate date boundaries after exact alignment:

- discovery starts 2023-09-15;
- discovery ends 2025-06-15;
- embargo covers 2025-06-16 through 2025-07-25;
- final holdout covers 2025-07-26 through 2025-11-22.

The final 720 bars must not be evaluated unless the frozen discovery gate passes. A failed discovery permanently closes v1.4 without reading holdout returns.

## External inputs

### Hyperliquid settled funding

Hourly public funding history is fetched for all eight assets over the price interval. It is aggregated to UTC-aligned 4-hour means. Every source file is hashed and recorded in a manifest.

### Stablecoin liquidity and macro risk

The existing provenance-checked Coin Metrics USDT/USDC market-capitalisation data and FRED VIX, broad-dollar and US ten-year Treasury series may be used only as lagged risk controls.

All daily external observations are delayed by one additional calendar day before use. Funding records must end before the execution candle opens.

## Signal timing

Every signal uses only completed candles through bar t. Any target change is filled at bar t+1 open. The first bar in a discovery period may use warm-up data but never its own close or later values.

## Sleeve 1: trend pullback or continuation

An asset is in a positive 4-hour trend only when all are true:

- close is above EMA-144;
- EMA-36 is above EMA-144;
- 60-bar return is positive;
- 144-bar efficiency ratio is at least 0.28.

A long entry requires either:

- pullback recovery: one of the previous three completed bars touched or closed below EMA-18, and the latest completed bar closes back above EMA-18 and above the previous bar high; or
- continuation: the latest completed close exceeds the prior 48-bar high, volume is at least 1.15 times its prior 48-bar median, and the close is no more than 1.75 ATR-20 above EMA-18.

Exit on the first completed-bar condition among:

- close below EMA-36;
- 2.5 ATR-20 trailing stop;
- 90-bar maximum holding period.

## Sleeve 2: range mean reversion

A range regime requires all are true:

- 72-bar efficiency ratio is at most 0.30;
- absolute 72-bar return is no greater than 1.25 times 72-bar realised volatility;
- EMA-36 and EMA-144 differ by no more than 1.0 ATR-20.

A long entry requires:

- close z-score versus the prior 48 closes is at most -1.50;
- RSI-14 is at most 35;
- the latest completed close is above the previous completed close.

Exit on the first completed-bar condition among:

- z-score reaches zero;
- RSI-14 reaches 55;
- 2.0 ATR-20 protective stop;
- 36-bar maximum holding period.

## Sleeve 3: negative-funding dislocation rebound

A funding dislocation requires all are true:

- trailing seven-day mean funding is negative;
- that mean is at or below the rolling 120-day tenth percentile for the same asset;
- the asset is at least 15% below its prior 120-bar closing high;
- the latest completed close crosses above EMA-12 after making a 20-bar closing low within the previous six bars.

Exit on the first completed-bar condition among:

- seven-day mean funding recovers to or above its rolling median;
- close reaches EMA-72;
- 2.25 ATR-20 protective stop;
- 60-bar maximum holding period.

## Frozen router and risk controls

- At most one sleeve may own an asset at a time.
- If multiple entries coincide, priority is funding rebound, trend, then range mean reversion.
- Rank eligible entries by sleeve-specific signal strength, then symbol for deterministic ties.
- Hold at most three assets.
- Maximum weight per asset: 25%.
- Minimum cash reserve: 25%.
- Gross exposure may never exceed 75% and leverage is forbidden.
- Inverse-volatility weighting uses trailing 60-bar 4-hour volatility.
- Portfolio volatility target: 30% annualised using sqrt(6 x 365).
- If both stablecoin liquidity and macro risk controls are adverse, multiply gross target by 0.75. Either control alone cannot force cash.
- Portfolio drawdown brake: 65% exposure multiplier after 5% drawdown, 25% after 10%, cash after 15%.
- Rebalance only when the target-value difference exceeds 4% of equity.

## Costs and accounting

Use the repository's existing crypto exchange fee, slippage and tax engines. Apply the existing additional cost stress of 0.15% per unit of turnover. Positions are marked on every 4-hour close and liquidated at the final period close for independent-period measurement.

## Frozen variants

1. primary multi-regime router;
2. router without trend sleeve;
3. router without range sleeve;
4. router without funding-rebound sleeve;
5. trend sleeve alone;
6. range sleeve alone;
7. funding-rebound sleeve alone;
8. cash;
9. equal-weight buy-and-hold benchmark.

No parameter search is permitted in v1.4. Only these frozen rules and ablations may be evaluated.

## Discovery acceptance gate

The primary router passes discovery only if every condition holds:

- exactly eight discovery periods are present;
- at least six periods are active;
- at least five periods are profitable;
- average, median and compounded net returns are positive;
- average extra-cost-stressed return is positive;
- the first four-period average and last four-period average are both positive;
- average return exceeds the equal-weight benchmark and the trend-only baseline;
- the primary beats the trend-only baseline in at least five of eight periods;
- worst period drawdown is at most 15%;
- at least five distinct assets are traded;
- no asset contributes more than 35% of gross traded notional;
- at least two of the three leave-one-sleeve-out variants have positive average return;
- at least six of eight leave-one-asset-out primary reruns have positive average return.

A discovery failure must still produce complete JSON and Markdown evidence and must not run the final holdout.

## One-shot final holdout gate

Only after a discovery pass, the same frozen code may evaluate the last 720 bars as three consecutive 240-bar periods. The holdout passes only if:

- at least two periods are active and profitable;
- average, compounded and stressed returns are positive;
- average return exceeds trend-only and equal-weight benchmarks;
- worst drawdown is at most 15%;
- at least three assets are traded;
- no asset contributes more than 45% of gross traded notional.

A holdout pass authorises only a time-limited shadow-paper candidate. It does not authorise continuous paper deployment, exchange connectivity or real-money trading.

## Failure discipline

- Missing data, incomplete funding, hash mismatch, timestamp misalignment, look-ahead detection or an invalid split fails closed.
- A software-successful rejected experiment may exit with the repository's research-rejection status while still uploading evidence.
- The final holdout cannot be reused, retuned or reinterpreted after first access.
