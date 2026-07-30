# Crypto Multi-Regime 4-Hour Discovery v1.4.1

## Status

This protocol correction is frozen before any v1.4 strategy return is calculated.

The original v1.4 protocol was frozen before a metadata-only coverage audit. That audit read no price returns and calculated no strategy returns. It found complete Coinbase hourly coverage for all original assets, but Hyperliquid funding began after the original 7 June 2023 start for DOT, NEAR and FIL, and no usable ICP funding existed in the audited range.

A second metadata-only audit confirmed:

- DOT funding begins 12 September 2023;
- NEAR funding begins 1 November 2023;
- FIL funding begins 14 November 2023;
- APT has complete Coinbase price coverage and Hyperliquid funding from 30 June 2023;
- all required assets have funding at the end of the frozen interval.

Therefore v1.4.1 makes only the following pre-return corrections:

1. replace ICP with APT;
2. move the start to the first clean UTC day after FIL funding begins;
3. shorten discovery from eight to six periods while preserving the same final holdout.

All alpha definitions, execution timing, costs, risk controls, ablations and holdout rules remain unchanged unless explicitly restated below.

## Paper-only boundary

The experiment is paper-only, long-or-cash, unlevered and cannot authorise real-money trading. Passing discovery only unlocks one use of the barred final holdout. Passing that holdout may only unlock a time-limited shadow-paper candidate.

## Coverage-corrected universe

The required Coinbase products and internal symbols are:

- AVAX-USD / AVAXUSDT
- DOT-USD / DOTUSDT
- NEAR-USD / NEARUSDT
- FIL-USD / FILUSDT
- APT-USD / APTUSDT
- OP-USD / OPUSDT
- ARB-USD / ARBUSDT
- SUI-USD / SUIUSDT

The universe remains disjoint from the earlier BTC, ETH, SOL, XRP, ADA, DOGE, LTC, BCH, LINK, XLM, ETC, ATOM, UNI and AAVE research universes.

## Frozen data interval

- Coinbase source granularity: one hour.
- Aggregate to UTC-aligned four-hour bars at 00:00, 04:00, 08:00, 12:00, 16:00 and 20:00 UTC.
- Start inclusive: 2023-11-15T00:00:00Z.
- End exclusive: 2025-11-23T00:00:00Z.
- Required hourly bars per asset: exactly 17,736.
- Required complete four-hour bars per asset: exactly 4,434.
- Missing hourly components invalidate a four-hour candle and fail the experiment.
- No price data dated 2025-11-23 or later may be fetched or evaluated.

Hyperliquid settled funding, Coin Metrics USDT/USDC market capitalisation and FRED VIX, broad-dollar and US ten-year Treasury series are fetched only for this frozen interval. All source files are hashed and recorded in manifests. Daily observations receive an additional one-calendar-day availability lag, and funding records must precede the execution candle.

## Frozen split

The 4,434 aligned four-hour bars are divided exactly as follows:

- warm-up: 600 bars;
- discovery: six consecutive non-overlapping periods of 480 bars each;
- embargo: 234 bars;
- barred final holdout: 720 bars.

Index boundaries:

- warm-up: 0-599;
- discovery periods: 600-3,479;
- embargo: 3,480-3,713;
- final holdout: 3,714-4,433.

Exact time boundaries after alignment:

- discovery starts 2024-02-23T00:00:00Z;
- discovery ends 2025-06-16T20:00:00Z;
- embargo runs 2025-06-17T00:00:00Z through 2025-07-25T20:00:00Z;
- final holdout runs 2025-07-26T00:00:00Z through 2025-11-22T20:00:00Z.

The final 720 bars must not be evaluated unless discovery passes every frozen gate. A discovery failure permanently closes this experiment without reading final-holdout returns.

## Alpha sleeves

The following rules are unchanged from v1.4.

### Trend pullback or continuation

Positive trend requires close above EMA-144, EMA-36 above EMA-144, positive 60-bar return and 144-bar efficiency ratio of at least 0.28.

Entry requires either:

- pullback recovery: one of the preceding three completed candles touches or closes below EMA-18, followed by a completed close above EMA-18 and above the previous candle high; or
- continuation: completed close above the prior 48-bar high, volume at least 1.15 times the prior 48-bar median, and close no more than 1.75 ATR-20 above EMA-18.

Exit on completed close below EMA-36, 2.5 ATR-20 trailing stop or 90-bar maximum hold.

### Range mean reversion

Range regime requires 72-bar efficiency ratio at most 0.30, absolute 72-bar return no greater than 1.25 times 72-bar realised volatility, and EMA-36/EMA-144 separation no greater than one ATR-20.

Entry requires close z-score versus the prior 48 closes at most -1.50, RSI-14 at most 35 and a completed close above the previous completed close.

Exit when z-score reaches zero, RSI-14 reaches 55, price breaches a 2.0 ATR-20 protective stop or 36 bars elapse.

### Negative-funding dislocation rebound

Entry requires trailing seven-day mean funding below zero and at or below its rolling 120-day tenth percentile, price at least 15% below the prior 120-bar closing high, and a completed cross above EMA-12 after a 20-bar closing low within the previous six bars.

Exit when seven-day funding reaches its rolling median, price reaches EMA-72, price breaches a 2.25 ATR-20 protective stop or 60 bars elapse.

## Router, execution and risk

- Signals use completed candles only; changes fill at the next four-hour open.
- One sleeve per asset; priority is funding rebound, trend, then range.
- Rank by frozen sleeve strength and deterministic symbol tie-break.
- Hold at most three assets.
- Maximum asset weight: 25%.
- Minimum cash reserve: 25%.
- Gross exposure cap: 75%; leverage forbidden.
- Inverse-volatility weighting uses 60 completed bars.
- Annualised portfolio-volatility target: 30% using sqrt(6 x 365).
- If stablecoin liquidity and macro controls are both adverse, multiply gross target by 0.75.
- Drawdown multipliers: 65% after 5%, 25% after 10%, cash after 15%.
- Rebalance only when target-value difference exceeds 4% of equity.
- Use existing crypto fee, slippage and tax engines plus 0.15% additional cost per unit turnover.
- Independent periods liquidate at their final close.

## Frozen variants

1. primary multi-regime router;
2. router without trend;
3. router without range;
4. router without funding rebound;
5. trend only;
6. range only;
7. funding rebound only;
8. cash;
9. equal-weight buy-and-hold benchmark.

No parameter search is permitted.

## Discovery gate

The primary passes discovery only if every condition holds:

- exactly six discovery periods;
- at least five active periods;
- at least four profitable periods;
- positive average, median and compounded net return;
- positive average extra-cost-stressed return;
- positive first-three-period and last-three-period averages;
- average return exceeds equal-weight buy-and-hold and trend-only;
- primary beats trend-only in at least four of six periods;
- worst period drawdown no greater than 15%;
- at least five distinct assets traded;
- no asset exceeds 35% of gross traded notional;
- at least two of three leave-one-sleeve-out variants have positive average return;
- at least six of eight leave-one-asset-out reruns have positive average return.

A rejected discovery must still upload complete JSON, Markdown, source manifests and hashes.

## One-shot final holdout gate

Only a discovery pass may unlock the last 720 bars as three consecutive 240-bar periods. The holdout passes only if:

- at least two periods are active and profitable;
- average, compounded and stressed returns are positive;
- average return exceeds trend-only and equal-weight benchmarks;
- worst drawdown is no greater than 15%;
- at least three assets are traded;
- no asset exceeds 45% of gross traded notional.

First access consumes the holdout regardless of result. A pass cannot authorise continuous paper trading or any real-money execution.

## Failure discipline

Missing data, timestamp misalignment, hash mismatch, source-coverage failure, look-ahead detection, an invalid split or any attempt to access the holdout before discovery acceptance fails closed. Thresholds cannot be relaxed after results are known.
