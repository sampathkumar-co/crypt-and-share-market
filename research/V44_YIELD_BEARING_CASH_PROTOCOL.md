# V4.4 Yield-Bearing Cash Protocol

## Status

Retrospective, paper-only research. This protocol does not authorize live trading.

## Purpose

V4.3 kept at least 90% of capital in cash but assigned zero return to that idle balance. V4.4 measures the same frozen v4.3 decisions while crediting idle cash with a public 3-month U.S. Treasury rate.

This is an accounting correction, not a new alpha claim.

## Frozen components

The following remain exactly as defined by v4.3:

- training, calibration and sealed date ranges;
- data features and completed-candle timing;
- model family and calibration search;
- regime classification and utility ranking;
- panic, utility, downside and disagreement thresholds;
- three-day rebalance cadence;
- long-or-cash behavior;
- 5% position size, 10% maximum target exposure;
- standard and stress transaction costs;
- next-bar execution assumptions;
- profitability and concentration gates.

## Cash source

- Provider: Federal Reserve Bank of St. Louis public FRED CSV.
- Series: `DGS3MO`, Market Yield on U.S. Treasury Securities at 3-Month Constant Maturity.
- Requested range: 2022-01-01 through 2026-06-30.
- Raw response SHA-256, observation count, first date and last date are recorded in the report.
- Missing or malformed source data fails closed.

## No-look-ahead rule

For portfolio date `D`, only the newest cash observation dated on or before `D-1` may be used. A same-day observation is never used. Weekends and holidays therefore carry forward the latest previously published observation.

If no prior observation exists, the campaign stops with an error.

## Compounding

The annual decimal yield `r` is converted to a daily compounded return:

`daily = (1 + r)^(1/365) - 1`

Each day, the return is applied only to the cash balance after any scheduled rebalance and transaction costs. Asset holdings receive the unchanged v4.3 asset returns.

## Verification

The campaign runs v4.3 and v4.4 on the same fitted bundle and records that:

- target-changing action counts are unchanged;
- selected assets are unchanged;
- signal and risk parameters are unchanged;
- standard, stress and annualized return uplift comes only from idle-cash yield.

## Interpretation

The sealed dates were already inspected during v4.3, so all v4.4 results are retrospective. Even if the numerical gates pass, v4.4 cannot become an untouched historical breakthrough. Independent source replication and a current-market paper smoke test remain required.
