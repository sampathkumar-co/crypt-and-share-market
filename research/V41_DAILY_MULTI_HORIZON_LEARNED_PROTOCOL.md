# v4.1 Daily Multi-Horizon Learned Crypto Protocol

Status: frozen before any v4.1 historical or current-market outcome is calculated.

## Motivation

The v4.0 intraday learned system completed a clean ten-minute current-market smoke run, but its 30-minute, two-hour, and six-hour forecasts could not clear the frozen 40-basis-point stress round-trip cost without violating the downside gate. Costs and risk limits are not relaxed.

v4.1 moves learned reasoning to daily data and longer forecast horizons where conservative execution costs are economically proportionate.

## Fixed universe and source

- BTC-USD;
- ETH-USD;
- SOL-USD;
- XRP-USD;
- ADA-USD.

Provider: Coinbase Exchange public daily candles.

- Exactly 1,000 completed UTC daily candles are required for every asset.
- Missing, duplicate, conflicting, or nonpositive candles fail closed.
- Every request URL and raw SHA-256 is recorded.
- Signals use completed day-D candles and fills occur at day D+1 UTC open.

## Multi-horizon learned targets

For every asset, models estimate absolute and market-relative return over:

- 1 day;
- 3 days;
- 7 days.

Separate classifiers estimate:

- probability of positive return after stress costs at each horizon;
- probability of a 3-day path loss greater than 2%;
- market regime: trend, chop, panic, or recovery.

The decision layer reasons over agreement and conflict:

- at least two horizons must predict positive absolute return after stress costs;
- the 3-day prediction is the primary ranking horizon;
- a negative 7-day forecast vetoes a long allocation;
- panic regime forces cash;
- high model disagreement or high downside probability forces rejection.

## Frozen numerical features

Per asset:

- 1, 3, 7, 14, 30, 60, 120, and 200-day returns;
- 7, 30, and 90-day realized volatility;
- 14 and 60-day trend efficiency;
- distance from 20, 50, 100, and 200-day moving averages;
- daily range, close location, and volume z-score;
- 30 and 90-day beta and correlation to BTC;
- relative strength versus the equal-weight market over 7, 30, and 90 days.

Market-wide:

- BTC multi-horizon returns and volatility;
- equal-weight market returns;
- breadth above 20, 50, and 200-day averages;
- cross-sectional dispersion;
- median volume shock;
- rolling average correlation.

No news, social, LLM, or manually entered feature is permitted in v4.1.

## Model family and selection

- Numerical machine learning only; no paid AI API.
- Histogram gradient boosting regression and classification ensembles.
- Three fixed seeds per learned target.
- Hyperparameter grid contains no more than eight configurations.
- Earliest 70% of eligible dates: training.
- Next 15%: calibration and fixed threshold selection.
- Final 15%: untouched verification, accessed once after selection.
- Splits are by date, never by shuffled rows.

## Decision cadence and paper execution

- Long-or-cash only.
- At most two selected assets.
- Maximum 5% target per asset.
- Maximum 10% total crypto exposure.
- Minimum 90% cash.
- Targets may change only every third completed daily candle, or immediately when panic forces cash.
- All fills use the next daily open.
- Natural drift is preserved between target-changing dates.
- Every verification segment begins and ends in cash and pays entry, rebalance, exit, and terminal liquidation costs.

## Frozen costs and risk governor

- Standard round-trip cost: 20 basis points.
- Stress round-trip cost: 40 basis points.
- Reject when 3-day downside probability exceeds 45%.
- Reject when ensemble disagreement exceeds the calibration-only 75th-percentile threshold.
- Reject when recent quote-volume proxy falls below the calibration-only 10th percentile.
- No leverage, shorts, derivatives, lending, averaging down, or exposure escalation.

## Historical breakthrough gate

A result is a v4.1 historical breakthrough candidate only when all conditions pass:

1. untouched standard-cost annualized return is at least 5%;
2. untouched stress-cost compounded return is positive;
3. maximum drawdown is no more than 10%;
4. five sequential untouched verification subwindows are positive at standard costs;
5. at least four of five are positive at stress costs;
6. at least 20 target-changing decisions occur in the untouched period;
7. BTC and at least two non-BTC assets are selected;
8. no one asset or subwindow supplies more than 70% of positive contribution;
9. untouched verification spans at least 90 calendar days;
10. the selected frozen model later reproduces on an independent price source.

Independent-source replication is intentionally false in the first Coinbase run.

## Current-market ten-minute smoke

After historical tests and serialization pass, the exact frozen bundle may run for ten minutes:

- public current prices polled every 30 seconds;
- no retraining during the clock;
- the daily target remains frozen unless a new completed UTC day appears;
- fictional portfolio mark-to-market, costs, data latency, errors, and final liquidation are recorded.

The ten-minute result validates operational behaviour only and cannot satisfy any profitability gate.

## Safety and isolation

- paper-only;
- `authorizes_trading=false`;
- `authorizes_shadow_paper=true` only for an isolated fictional ledger;
- no credentials, wallets, orders, or capital;
- Track A, v3.1.2, v3.2, v3.3, and all evidence remain unchanged;
- v4.1 cannot replace the verified BTC/ETH baseline without historical replication and forward paper evidence.
